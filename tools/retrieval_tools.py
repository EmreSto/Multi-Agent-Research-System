import json
import logging
from pathlib import Path
import chromadb
from anthropic import Anthropic
from dotenv import load_dotenv
import pymupdf4llm
from tools.chunking import _split_into_sections, _subsplit_large_sections
from schemas.chunk_schemas import Chunk
from concurrent.futures import ThreadPoolExecutor , as_completed
from config.agent_config import MODELS,RCS_CONFIG
from tools.registry import ToolRegistry


load_dotenv()

#Paths
PROJECT_ROOT = Path(__file__).parent.parent
VECTORDB_DIR = PROJECT_ROOT / "data" / "vectordb"
VECTORDB_DIR.mkdir(parents=True, exist_ok=True)

#Clients
_chroma_client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
_haiku_client = Anthropic()
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
        "and relevance scoring. Returns scored summaries ordered for optimal "
        "comprehension. Use this instead of parse_pdf for teaching."
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
        doc = pymupdf4llm.to_markdown(file_path)
        title = _extract_paper_title(doc)
        sections= _split_into_sections(doc)
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

RCS_SYSTEM_PROMPT = (
    "You are a relevance scoring assistant for academic paper retrieval."
    " You receive a chunk from a paper and a research question."
    " Your job: score relevance and write a summary that replaces the original chunk"
    " for downstream use."
    "\n\nRespond ONLY with valid JSON:"
    '\n{{"summary": "...", "relevance_score": 7}}'
    "\n\n## Scoring rubric"
    "\n- 0: Completely irrelevant to the question"
    "\n- 1-3: Tangentially related, background info only"
    "\n- 4-6: Partially relevant, contains useful context"
    " but doesn't address the question directly"
    "\n- 7-8: Clearly relevant, contains claims, data,"
    " or methods that help answer the question"
    "\n- 9-10: Directly answers the question with specific evidence"
    "\n\n## Summary rules"
    "\n- Write a compressed replacement for the chunk ,the reader will NOT see the original text"
    "\n- Preserve exact numbers, variable names, equations, and key relationships"
    "\n- Focus on how the content connects to the question"
    '\n- If the chunk is irrelevant (score 0-3), return an empty summary: ""'
    "\n- Max {summary_length} words"
)

RCS_USER_PROMPT = (
    "Source: {paper_title}, {section_name}, Page {page_number}"
    "\n\n---\n\n{chunk_text}\n\n---\n\n"
    "Question: {question}"
)

def _score_chunk_with_haiku(query: str, chunk_content: str, chunk_metadata: dict) -> dict | None:
    formatted_system_prompt = RCS_SYSTEM_PROMPT.format(summary_length=RCS_CONFIG["summary_length"])
    formatted_user_prompt = RCS_USER_PROMPT.format(
        paper_title= chunk_metadata.get("paper_title", "Unknown Title"),
        section_name= chunk_metadata.get("section_name", "Unknown Section"),
        page_number= chunk_metadata.get("page_number", "N/A"),
        chunk_text= chunk_content,
        question= query
    )
    try:
        response = _haiku_client.messages.create(
            model=MODELS["haiku"],
            max_tokens=512,
            system=formatted_system_prompt,
            messages=[{"role": "user", "content": formatted_user_prompt}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(raw)
        return {
            "relevance_score": int(parsed.get("relevance_score")),
            "summary": parsed["summary"],
            "section_name": chunk_metadata.get("section_name", ""),
            "paper_title": chunk_metadata.get("paper_title", ""),
            "paper_id": chunk_metadata.get("paper_id", ""),
            "chunk_index": chunk_metadata.get("chunk_index", 0),
            "has_equations": chunk_metadata.get("has_equations", False),
            "raw_text": chunk_content,
        }
    except Exception as e:
        logging.warning(f"RCS scoring failed: {e}")
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
    collection =_get_collection()
    results = collection.query(**query_kwargs)
    if not results["documents"][0]:
        return json.dumps({"status": "no_results", "message": "No relevant chunks found."})
    zipped = zip(results["documents"][0], results["metadatas"][0])
    scored_chunks = []
    with ThreadPoolExecutor(max_workers=RCS_CONFIG["max_workers"]) as executor:
        futures = [executor.submit(_score_chunk_with_haiku, query, doc, meta) for doc, meta in zipped]
        for future in as_completed(futures):
            result = future.result()
            if result is not None and result["relevance_score"] >= RCS_CONFIG["relevance_threshold"]:
                scored_chunks.append(result)
    if not scored_chunks:
        return json.dumps({"status": "no_relevant_chunks", "message": f"No chunks passed the relevance threshold of {RCS_CONFIG['relevance_threshold']}."})

    scored_chunks = sorted(scored_chunks, key= lambda x: x["relevance_score"], reverse=True)
    for chunk in scored_chunks:
        if chunk["relevance_score"] < 9:
            chunk.pop("raw_text", None)
    front = scored_chunks[::2]
    back = scored_chunks[1::2]
    ordered = front + list(reversed(back))
    return json.dumps({"status": "success", "query": query, "paper_id": paper_id, "total_candidates": len(results["documents"][0]), "passed_threshold": len(ordered), "ordered_summaries": ordered})


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



    

    

