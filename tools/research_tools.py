import json
import logging
import re
import time
from pathlib import Path

import arxiv
import fitz
from tools.registry import ToolRegistry

#Paths
PROJECT_ROOT = Path(__file__).parent.parent
SOURCES_DIR = PROJECT_ROOT / "sources"
SOURCES_DIR.mkdir(exist_ok=True)
logger = logging.getLogger(__name__)

#Shared arXiv client
_arxiv_client = arxiv.Client(
    page_size=50,
    delay_seconds=5.0,
    num_retries=5,
)

#Schemas

SEARCH_ARXIV_SCHEMA = {
    "name": "search_arxiv",
    "description": (
        "Search arXiv for academic papers by topic, author, or arXiv ID. "
        "Returns titles, authors, abstracts, publication dates, and PDF URLs. "
        "Use this to find relevant papers before teaching or briefing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — topic keywords, author name, or arXiv ID (e.g. '2301.01234').",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return. Default 10, max 50.",
            },
        },
        "required": ["query"],
    },
}

#Handlers

def search_arxiv(tool_input: dict) -> str:
    query = tool_input["query"]
    max_results = min(tool_input.get("max_results", 10), 50)

    logger.info(f"search_arxiv: query='{query}', max_results={max_results}")

    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
    )

    max_retries = 3
    results = []
    for attempt in range(max_retries + 1):
        try:
            results = []
            for paper in _arxiv_client.results(search):
                results.append({
                    "title": paper.title,
                    "authors": [a.name for a in paper.authors],
                    "abstract": paper.summary,
                    "published": paper.published.strftime("%Y-%m-%d"),
                    "updated": paper.updated.strftime("%Y-%m-%d"),
                    "arxiv_id": paper.entry_id,
                    "pdf_url": paper.pdf_url,
                    "categories": paper.categories,
                })
            break
        except (arxiv.HTTPError, arxiv.UnexpectedEmptyPageError) as e:
            if attempt < max_retries:
                backoff = 10 * (2 ** attempt)
                logger.warning(
                    f"search_arxiv: arXiv API error (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {backoff}s: {e}"
                )
                time.sleep(backoff)
            else:
                logger.error(f"search_arxiv: failed after {max_retries + 1} attempts: {e}")
                return json.dumps({
                    "error": "arxiv_rate_limited",
                    "message": (
                        f"arXiv API is rate-limiting requests after {max_retries + 1} attempts. "
                        "Do NOT retry. Wait at least 10 minutes before trying again."
                    ),
                })

    if not results:
        return json.dumps({"message": f"No papers found for query: '{query}'"})

    return json.dumps(results, indent=2)



PARSE_PDF_SCHEMA = {
    "name": "parse_pdf",
    "description": (
        "Extract text from a PDF file in the sources/ directory. "
        "Returns structured text with page markers for citation. "
        "Use start_page/end_page to read in chunks if the paper is long. "
        "Call list_sources first to see available PDFs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to PDF file (e.g. 'sources/1011.6402v3.pdf').",
            },
            "start_page": {
                "type": "integer",
                "description": "First page to extract (0-indexed). Default: 0.",
            },
            "end_page": {
                "type": "integer",
                "description": "Last page to extract (exclusive). Default: all pages.",
            },
        },
        "required": ["file_path"],
    },
}

#Equation detection
_MATH_OPERATORS = "∑∫∏∂∇∆√∞≈≠≤≥±∈∉⊂⊃∀∃∧∨¬⇒⇔←→↔"
_GREEK_LETTERS = "αβγδεζηθικλμνξπρστυφχψω"
_MATH_SYMBOLS = set(_MATH_OPERATORS + _GREEK_LETTERS)

_LATEX_COMMANDS = (
    "sum", "int", "frac", "partial", "nabla", "sqrt",
    "infty", "approx", "leq", "geq", "pm", "in", "forall", "exists",
)
_LATEX_PATTERN = re.compile(r"\\(?:" + "|".join(_LATEX_COMMANDS) + r")")

_ASSIGNMENT_PATTERN = re.compile(r"\b[A-Za-z]\s*[=<>≤≥]\s*")


def _looks_like_equation(line: str) -> bool:
    if any(ch in _MATH_SYMBOLS for ch in line):
        return True
    if _LATEX_PATTERN.search(line):
        return True
    if _ASSIGNMENT_PATTERN.search(line):
        return True
    return False


def _tag_equations(text: str) -> str:
    lines = text.split("\n")
    tagged = []
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) < 200 and _looks_like_equation(stripped):
            tagged.append(f"[EQUATION] {line}")
        else:
            tagged.append(line)
    return "\n".join(tagged)


def parse_pdf(tool_input: dict) -> str:
    file_path = tool_input["file_path"]
    start_page = tool_input.get("start_page", 0)

    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if not path.exists():
        return json.dumps({"error": "file_not_found", "message": f"PDF not found: {file_path}"})

    logger.info(f"parse_pdf: file='{path}', start_page={start_page}")

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        return json.dumps({"error": "pdf_open_error", "message": str(e)})

    total_pages = len(doc)
    end_page = min(tool_input.get("end_page", total_pages), total_pages)

    pages_text = []
    for page_num in range(start_page, end_page):
        page = doc[page_num]
        text = page.get_text()
        tagged = _tag_equations(text)
        pages_text.append(f"\n--- Page {page_num + 1} ---\n{tagged}")

    doc.close()

    full_text = "\n".join(pages_text)

    result = {
        "file": str(path.name),
        "total_pages": total_pages,
        "pages_extracted": f"{start_page + 1}-{end_page}",
        "total_chars": len(full_text),
        "text": full_text,
    }

    if end_page < total_pages:
        result["note"] = (
            f"Showing pages {start_page + 1}-{end_page} of {total_pages}. "
            f"Call parse_pdf again with start_page={end_page} to continue reading."
        )

    return json.dumps(result)


LIST_SOURCES_SCHEMA = {
    "name": "list_sources",
    "description": (
        "List available PDF files in the sources/ directory. "
        "Call this first to see what papers are available before using parse_pdf."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}


def list_sources(_tool_input: dict) -> str:
    pdfs = sorted(SOURCES_DIR.glob("*.pdf"))
    if not pdfs:
        return json.dumps({"message": "No PDF files found in sources/."})

    results = []
    for pdf in pdfs:
        size_kb = pdf.stat().st_size / 1024
        try:
            doc = fitz.open(str(pdf))
            pages = len(doc)
            doc.close()
        except Exception:
            pages = "unknown"

        results.append({
            "filename": pdf.name,
            "path": f"sources/{pdf.name}",
            "size_kb": round(size_kb, 1),
            "pages": pages,
        })

    return json.dumps(results, indent=2)


#Registration

def register_research_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="search_arxiv",
        schema=SEARCH_ARXIV_SCHEMA,
        handler=search_arxiv,
        category="research",
    )
    registry.register(
        name="parse_pdf",
        schema=PARSE_PDF_SCHEMA,
        handler=parse_pdf,
        category="research",
    )
    registry.register(
        name="list_sources",
        schema=LIST_SOURCES_SCHEMA,
        handler=list_sources,
        category="research",
    )
