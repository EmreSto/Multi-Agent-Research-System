import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import chromadb
import pymupdf4llm
from anthropic import RateLimitError
from config.agent_config import MODELS, RCS_CONFIG
from config.clients import sync_client as _haiku_client
from schemas.chunk_schemas import Chunk
from tools.chunking import _split_into_sections, _subsplit_large_sections
from tools.registry import ToolRegistry


#Simple throttle: serialize Haiku calls so we stay under the 50 RPM ceiling.
_HAIKU_MIN_INTERVAL = 60.0 / 40  # 40 RPM target leaves headroom under 50.
_haiku_throttle_lock = threading.Lock()
_haiku_last_call_ts = 0.0


def _throttle_haiku() -> None:
    global _haiku_last_call_ts
    with _haiku_throttle_lock:
        now = time.monotonic()
        wait = _HAIKU_MIN_INTERVAL - (now - _haiku_last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _haiku_last_call_ts = time.monotonic()


#Paths
PROJECT_ROOT = Path(__file__).parent.parent
VECTORDB_DIR = PROJECT_ROOT / "data" / "vectordb"
VECTORDB_DIR.mkdir(parents=True, exist_ok=True)

#Clients
_chroma_client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
COLLECTION_NAME = "paper_chunks"

def _get_collection():
    return _chroma_client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


#Schemas

INGEST_PAPER_SCHEMA = {
    "name": "ingest_paper",
    "description": (
        "Ingest a PDF paper into the vector database for chunk-based retrieval. "
        "Call this once per paper before using retrieve_chunks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to PDF file (e.g. 'sources/1011.6402v3.pdf').",
            },
        },
        "required": ["file_path"],
    },
}

RETRIEVE_CHUNKS_SCHEMA = {
    "name": "retrieve_chunks",
    "description": (
        "Retrieve relevant chunks from an ingested paper using semantic search "
        "plus Haiku relevance scoring. Returns the full raw text of every chunk "
        "that passes the relevance threshold (score >= 7), ordered for optimal "
        "comprehension (highest-scored chunks at the edges to mitigate "
        "lost-in-the-middle). Quote from raw_text for VERIFIED claims. Use "
        "this instead of parse_pdf for teaching."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Research question or topic to retrieve relevant chunks for.",
            },
            "paper_id": {
                "type": "string",
                "description": "Filter to a specific paper (filename without extension, e.g. '1011.6402v3').",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of candidate chunks to retrieve before scoring. Default 15.",
            },
        },
        "required": ["query"],
    },
}



#Helpers

def _extract_paper_title(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    for line in lines:
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return "Untitled Paper"

def _ingest_chunks(chunks: list[Chunk], paper_id: str) -> int:
    collection = _get_collection()
    ids = [f"{paper_id}_chunk_{c.chunk_index}" for c in chunks]
    documents = [c.content for c in chunks]
    metadatas = [{
        "paper_id":c.paper_id,
        "paper_title": c.paper_title,
        "section_name": c.section_name,
        "has_equations": c.has_equations,
        "token_count": c.token_count,
        "page_number": c.page_number if c.page_number is not None else -1,
        "chunk_index": c.chunk_index,
    } for c in chunks]
    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return len(ids)


#Ingestion

def ingest_paper(tool_input: dict) -> str:
    file_path = tool_input.get("file_path")
    if not file_path:
        return json.dumps({"error": "missing_path", "message": "The 'file_path' field is required."})
    paper_id = Path(file_path).stem
    logging.info(f"Processing paper: {paper_id}")
    if _get_collection().get(where={"paper_id": paper_id}, limit=1)["ids"]:
        return json.dumps({"status": "already_ingested", "paper_id": paper_id, "message": f"Paper with ID {paper_id} is already ingested."})
    try:
        pages = pymupdf4llm.to_markdown(file_path, page_chunks=True)
        full_text = "\n".join(p.get("text", "") for p in pages)
        title = _extract_paper_title(full_text)
        sections = _split_into_sections(pages)
        subsections = _subsplit_large_sections(sections, max_tokens=1500)
        for chunk in subsections:
            chunk.paper_title = title
            chunk.paper_id = paper_id
        count = _ingest_chunks(subsections, paper_id)
        return json.dumps({"status": "success", "paper_id": paper_id, "paper_title": title, "chunks_stored": count, "message": f"Paper '{title}' ingested successfully with {count} chunks."})
    except Exception as e:
        logging.error(f"Error processing paper {paper_id}: {str(e)}")
        return json.dumps({"error": "ingestion_failed", "paper_id": paper_id, "message": f"Failed to ingest paper: {str(e)}"})

#RCS scoring

EMIT_SCORE_TOOL = {
    "name": "emit_score",
    "description": (
        "Emit a relevance score (0-10) for a chunk of a paper given a research "
        "question. Call this exactly once per chunk."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "relevance_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": (
                    "0 = irrelevant, 7+ = clearly relevant, 10 = directly answers."
                ),
            },
        },
        "required": ["relevance_score"],
    },
}


RCS_SYSTEM_PROMPT = (
    "You are a relevance scoring assistant for academic paper retrieval. "
    "You receive a chunk from a paper and a research question.\n\n"
    "You MUST call the emit_score tool with a single integer 0-10. Do not "
    "produce any free-form output.\n\n"
    "## Scoring rubric\n"
    "- 0: Completely irrelevant to the question\n"
    "- 1-3: Tangentially related, background info only\n"
    "- 4-6: Partially relevant, contains useful context but does not directly "
    "address the question\n"
    "- 7-8: Clearly relevant — contains claims, data, or methods that help "
    "answer the question\n"
    "- 9-10: Directly answers the question with specific evidence"
)


RCS_USER_PROMPT = (
    "Source: {paper_title}, {section_name}, Page {page_number}\n\n"
    "---\n\n{chunk_text}\n\n---\n\n"
    "Question: {question}"
)


def _score_chunk_with_haiku(query: str, chunk_content: str, chunk_metadata: dict) -> dict | None:
    formatted_user_prompt = RCS_USER_PROMPT.format(
        paper_title=chunk_metadata.get("paper_title", "Unknown Title"),
        section_name=chunk_metadata.get("section_name", "Unknown Section"),
        page_number=chunk_metadata.get("page_number", "N/A"),
        chunk_text=chunk_content,
        question=query,
    )
    for attempt in range(4):
        try:
            _throttle_haiku()
            response = _haiku_client.messages.create(
                model=MODELS["haiku"],
                max_tokens=256,
                system=RCS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": formatted_user_prompt}],
                tools=[EMIT_SCORE_TOOL],
                tool_choice={"type": "tool", "name": "emit_score"},
            )
            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if tool_use is None:
                logging.warning("RCS: Haiku did not emit emit_score tool_use")
                return None
            score = int(tool_use.input["relevance_score"])
            return {
                "relevance_score": score,
                "section_name": chunk_metadata.get("section_name", ""),
                "paper_title": chunk_metadata.get("paper_title", ""),
                "paper_id": chunk_metadata.get("paper_id", ""),
                "chunk_index": chunk_metadata.get("chunk_index", 0),
                "page_number": chunk_metadata.get("page_number", -1),
                "has_equations": chunk_metadata.get("has_equations", False),
                "raw_text": chunk_content,
            }
        except RateLimitError:
            if attempt < 3:
                backoff = 2 ** attempt + 1.0
                logging.warning(
                    f"RCS rate-limited, backing off {backoff:.1f}s (attempt {attempt + 1}/4)"
                )
                time.sleep(backoff)
                continue
            logging.warning("RCS: exhausted retries on rate limit")
            return None
        except Exception as e:
            logging.warning(f"RCS scoring failed: {e}")
            return None
    return None


#Retrieval

def retrieve_chunks(tool_input: dict) -> str:
    query = tool_input.get("query")
    paper_id = tool_input.get("paper_id")
    top_k = tool_input.get("top_k", RCS_CONFIG["top_k"])
    if not query:
        return json.dumps({"error": "missing_query", "message": "The 'query' field is required."})
    query_kwargs = {"query_texts": [query], "n_results": top_k}
    if paper_id:
        query_kwargs["where"] = {"paper_id": paper_id}
    collection = _get_collection()
    results = collection.query(**query_kwargs)
    if not results["documents"][0]:
        return json.dumps({"status": "no_results", "message": "No relevant chunks found."})

    zipped = list(zip(results["documents"][0], results["metadatas"][0]))
    all_results: list[dict | None] = []
    with ThreadPoolExecutor(max_workers=RCS_CONFIG["max_workers"]) as executor:
        futures = [executor.submit(_score_chunk_with_haiku, query, doc, meta) for doc, meta in zipped]
        for future in as_completed(futures):
            all_results.append(future.result())

    scoring_failures = sum(1 for r in all_results if r is None)
    scored_chunks = [
        r for r in all_results
        if r is not None and r["relevance_score"] >= RCS_CONFIG["relevance_threshold"]
    ]

    if not scored_chunks:
        return json.dumps({
            "status": "no_relevant_chunks",
            "message": f"No chunks passed the relevance threshold of {RCS_CONFIG['relevance_threshold']}.",
            "scoring_failures": scoring_failures,
        })

    scored_chunks = sorted(scored_chunks, key=lambda x: x["relevance_score"], reverse=True)
    front = scored_chunks[::2]
    back = scored_chunks[1::2]
    ordered = front + list(reversed(back))
    return json.dumps({
        "status": "success",
        "query": query,
        "paper_id": paper_id,
        "total_candidates": len(results["documents"][0]),
        "scoring_failures": scoring_failures,
        "passed_threshold": len(ordered),
        "ordered_chunks": ordered,
    })


#Registration

def register_retrieval_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="ingest_paper",
        schema=INGEST_PAPER_SCHEMA,
        handler=ingest_paper,
        category="retrieval"
    )
    registry.register(
        name="retrieve_chunks",
        schema=RETRIEVE_CHUNKS_SCHEMA,
        handler=retrieve_chunks,
        category="retrieval"
    )







