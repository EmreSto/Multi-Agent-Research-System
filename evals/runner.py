"""Runs Teacher against gold claims and RECALLED-trap queries; saves raw outputs.

Usage:
    python -m evals.runner <output_name>
    python evals/runner.py <output_name>

If <output_name> is omitted a timestamped name is used.
Results land in evals/results/<output_name>.json.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base_agent import call_agent_async
from tools.registry import ToolRegistry
from tools.research_tools import register_research_tools
from tools.retrieval_tools import ingest_paper, register_retrieval_tools, retrieve_chunks


EVALS_DIR = Path(__file__).resolve().parent
PAPERS_DIR = EVALS_DIR / "papers"
RESULTS_DIR = EVALS_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
PROJECT_ROOT = EVALS_DIR.parent


QUERY_TEMPLATE = (
    'The paper "{paper_title}" is already ingested in the retrieval system '
    '(paper_id: {paper_id}). Use retrieve_chunks with this paper_id to find '
    "relevant passages, then answer the following question concisely with "
    "the quote-then-claim format and a confidence tag:\n\n"
    "{query}"
)


async def _run_one_query(agent: str, query: str, registry: ToolRegistry) -> dict:
    result = await call_agent_async(agent, query, registry=registry)
    return {
        "text": result.get("text", ""),
        "confidence_summary": result.get("confidence_summary", {}),
        "model": result.get("model"),
        "cost": result.get("cost", 0),
        "latency": result.get("latency", 0),
        "tool_iterations": result.get("tool_iterations", 0),
    }


async def run_gold_file(gold_path: Path, registry: ToolRegistry) -> dict:
    gold = json.loads(gold_path.read_text())
    paper_id = gold["paper_id"]
    paper_title = gold["paper_title"]
    source_file = gold["source_file"]

    abs_source = (PROJECT_ROOT / source_file).resolve()
    ingest_payload = {"file_path": str(abs_source)}
    ingest_result_raw = ingest_paper(ingest_payload)
    ingest_result = json.loads(ingest_result_raw)
    print(f"[ingest] {paper_id}: {ingest_result.get('status', 'unknown')}")

    paper_results = {
        "paper_id": paper_id,
        "paper_title": paper_title,
        "gold_file": gold_path.name,
        "ingest_result": ingest_result,
        "claim_results": [],
        "trap_results": [],
    }

    for claim in gold.get("claims", []):
        print(f"  [claim {claim['id']}] {claim['query']}")

        retrieval_raw = retrieve_chunks({
            "query": claim["query"],
            "paper_id": paper_id,
            "top_k": 15,
        })
        retrieval = json.loads(retrieval_raw)

        teacher_query = QUERY_TEMPLATE.format(
            paper_title=paper_title,
            paper_id=paper_id,
            query=claim["query"],
        )
        teacher = await _run_one_query("teacher", teacher_query, registry)

        paper_results["claim_results"].append({
            "claim_id": claim["id"],
            "gold": claim,
            "retrieval": retrieval,
            "teacher": teacher,
        })

    for trap in gold.get("traps", []):
        print(f"  [trap  {trap['id']}] {trap['query']}")

        teacher_query = QUERY_TEMPLATE.format(
            paper_title=paper_title,
            paper_id=paper_id,
            query=trap["query"],
        )
        teacher = await _run_one_query("teacher", teacher_query, registry)

        paper_results["trap_results"].append({
            "trap_id": trap["id"],
            "gold": trap,
            "teacher": teacher,
        })

    return paper_results


async def run_all(output_name: str) -> Path:
    logging.basicConfig(level=logging.WARNING)

    registry = ToolRegistry()
    register_research_tools(registry)
    register_retrieval_tools(registry)

    all_results = {
        "run_name": output_name,
        "run_timestamp": datetime.now().isoformat(),
        "papers": [],
    }

    gold_files = sorted(PAPERS_DIR.glob("*.json"))
    if not gold_files:
        raise SystemExit(f"no gold files found in {PAPERS_DIR}")

    for gold_file in gold_files:
        print(f"\n{'=' * 60}\n{gold_file.name}\n{'=' * 60}")
        paper_result = await run_gold_file(gold_file, registry)
        all_results["papers"].append(paper_result)

    output_path = RESULTS_DIR / f"{output_name}.json"
    output_path.write_text(json.dumps(all_results, indent=2, default=str))

    total_cost = sum(
        cr["teacher"]["cost"]
        for p in all_results["papers"]
        for cr in p["claim_results"] + p["trap_results"]
    )
    total_latency = sum(
        cr["teacher"]["latency"]
        for p in all_results["papers"]
        for cr in p["claim_results"] + p["trap_results"]
    )
    print(f"\nResults saved to {output_path}")
    print(f"Total Teacher cost: ${total_cost:.4f} | Total latency: {total_latency:.1f}s")
    return output_path


def _default_name() -> str:
    return f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else _default_name()
    asyncio.run(run_all(name))
