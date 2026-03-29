import json
import logging
import time

import arxiv
# import requests  # uncomment when enabling Semantic Scholar

from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Shared client — rate limiting state (delay between requests) persists
# across multiple tool calls within a session, preventing 429 floods.
_arxiv_client = arxiv.Client(
    page_size=50,         # match our max_results cap (default 100 is wasteful)
    delay_seconds=5.0,    # 5s between requests (arXiv requires ≥3s)
    num_retries=5,        # more retries for transient 429/503
)

# --- Tool schemas (Anthropic API format) ---

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

# --- Tool handlers ---

def search_arxiv(tool_input: dict) -> str:
    """Search arXiv and return structured results."""
    query = tool_input["query"]
    max_results = min(tool_input.get("max_results", 10), 50)

    logger.info(f"search_arxiv: query='{query}', max_results={max_results}")

    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
    )

    # Application-level retry with exponential backoff.
    # The arxiv library retries internally (5 attempts × 5s delay), but if
    # arXiv is already rate-limiting us, we need longer backoff between rounds.
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
            break  # Success
        except (arxiv.HTTPError, arxiv.UnexpectedEmptyPageError) as e:
            if attempt < max_retries:
                backoff = 10 * (2 ** attempt)  # 10s, 20s, 40s
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



# --- Semantic Scholar (commented out — enable when you have an API key or rate limit resets) ---
#
# SEARCH_SEMANTIC_SCHOLAR_SCHEMA = {
#     "name": "search_semantic_scholar",
#     "description": (
#         "Search Semantic Scholar for academic papers. "
#         "Covers arXiv, PubMed, ACL, and many other sources. "
#         "More reliable than arXiv API (higher rate limits). "
#         "Returns titles, authors, abstracts, citation counts, and PDF links."
#     ),
#     "input_schema": {
#         "type": "object",
#         "properties": {
#             "query": {
#                 "type": "string",
#                 "description": "Search query — topic keywords or paper title.",
#             },
#             "max_results": {
#                 "type": "integer",
#                 "description": "Maximum number of results to return. Default 10, max 100.",
#             },
#         },
#         "required": ["query"],
#     },
# }
#
#
# def search_semantic_scholar(tool_input: dict) -> str:
#     """Search Semantic Scholar and return structured results."""
#     import requests
#
#     query = tool_input["query"]
#     max_results = min(tool_input.get("max_results", 10), 100)
#
#     logger.info(f"search_semantic_scholar: query='{query}', max_results={max_results}")
#
#     url = "https://api.semanticscholar.org/graph/v1/paper/search"
#     params = {
#         "query": query,
#         "limit": max_results,
#         "fields": "title,authors,abstract,year,externalIds,url,citationCount,publicationDate,openAccessPdf",
#     }
#     headers = {"User-Agent": "ResearchCrew/0.1 (academic-research-tool)"}
#
#     max_retries = 3
#     for attempt in range(max_retries + 1):
#         try:
#             resp = requests.get(url, params=params, headers=headers, timeout=30)
#             resp.raise_for_status()
#             break
#         except requests.exceptions.HTTPError as e:
#             if resp.status_code == 429 and attempt < max_retries:
#                 backoff = 5 * (2 ** attempt)
#                 logger.warning(
#                     f"search_semantic_scholar: rate limited (attempt {attempt + 1}/{max_retries + 1}), "
#                     f"retrying in {backoff}s"
#                 )
#                 time.sleep(backoff)
#             else:
#                 logger.error(f"search_semantic_scholar: API error: {e}")
#                 return json.dumps({"error": "semantic_scholar_error", "message": str(e)})
#         except requests.exceptions.RequestException as e:
#             logger.error(f"search_semantic_scholar: request failed: {e}")
#             return json.dumps({"error": "semantic_scholar_error", "message": str(e)})
#
#     data = resp.json()
#     papers = data.get("data", [])
#     if not papers:
#         return json.dumps({"message": f"No papers found for query: '{query}'"})
#
#     results = []
#     for paper in papers:
#         external_ids = paper.get("externalIds") or {}
#         arxiv_id = external_ids.get("ArXiv")
#         pdf_url = None
#         oap = paper.get("openAccessPdf")
#         if oap:
#             pdf_url = oap.get("url")
#         elif arxiv_id:
#             pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
#         results.append({
#             "title": paper.get("title"),
#             "authors": [a["name"] for a in (paper.get("authors") or [])],
#             "abstract": paper.get("abstract"),
#             "published": paper.get("publicationDate"),
#             "year": paper.get("year"),
#             "citation_count": paper.get("citationCount"),
#             "arxiv_id": arxiv_id,
#             "pdf_url": pdf_url,
#             "url": paper.get("url"),
#         })
#     return json.dumps(results, indent=2)


# --- Registration ---

def register_research_tools(registry: ToolRegistry) -> None:
    """Register all research tools with the registry."""
    registry.register(
        name="search_arxiv",
        schema=SEARCH_ARXIV_SCHEMA,
        handler=search_arxiv,
        category="research",
    )
    # Uncomment to enable Semantic Scholar:
    # registry.register(
    #     name="search_semantic_scholar",
    #     schema=SEARCH_SEMANTIC_SCHOLAR_SCHEMA,
    #     handler=search_semantic_scholar,
    #     category="research",
    # )
