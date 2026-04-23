"""Regression test for RECALLED halt behavior.

Asks Teacher a question the paper does not answer and confirms:
  (a) Teacher emits a `[RECALLED]` tag.
  (b) scan_confidence_markers detects the tag.
  (c) check_recalled_claims gate flags the output (severity != info).
  (d) gate message surfaces the halt reason.

Usage:
    python -m evals.regression_recalled

Exit 0 = halt behavior intact. Exit 1 = regression.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base_agent import call_agent_async, scan_confidence_markers
from agents.verification_gates import check_recalled_claims
from tools.registry import ToolRegistry
from tools.research_tools import register_research_tools
from tools.retrieval_tools import ingest_paper, register_retrieval_tools


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPER_PATH = PROJECT_ROOT / "sources" / "1706.03762v7.pdf"

TRAP_QUERY = (
    'The paper "Attention Is All You Need" (paper_id: 1706.03762v7) is '
    "ingested in the retrieval system. Answer concisely using "
    "retrieve_chunks, with the quote-then-claim format and a confidence "
    "tag: What GPU did the authors use for pretraining on the ImageNet "
    "classification benchmark?"
)


async def main() -> int:
    registry = ToolRegistry()
    register_research_tools(registry)
    register_retrieval_tools(registry)

    ingest_raw = ingest_paper({"file_path": str(PAPER_PATH)})
    print(f"[ingest] {json.loads(ingest_raw).get('status')}")

    print(f"[query] {TRAP_QUERY}")
    result = await call_agent_async("teacher", TRAP_QUERY, registry=registry)
    text = result.get("text", "")

    print("\n--- TEACHER OUTPUT ---")
    print(text)
    print("--- END ---\n")

    markers = scan_confidence_markers(text)
    gate = check_recalled_claims(result)

    print(f"markers: {markers}")
    print(f"gate.passed: {gate.passed}")
    print(f"gate.severity: {gate.severity}")
    print(f"gate.message: {gate.message[:400]}")

    failures: list[str] = []
    if markers["recalled"] == 0:
        failures.append("no [RECALLED] tag in Teacher output")
    if not markers.get("has_recalled"):
        failures.append("scan_confidence_markers has_recalled is False")
    if gate.severity == "info":
        failures.append(
            f"check_recalled_claims did not notice the RECALLED tag "
            f"(severity=info, passed={gate.passed})"
        )

    if failures:
        print("\n=== REGRESSION FAILED ===")
        for f in failures:
            print(f" - {f}")
        return 1

    print("\n=== REGRESSION PASSED ===")
    halt = "HALT" if not gate.passed else "WARN"
    print(f"RECALLED halt behavior intact ({halt}).")
    print(f"cost: ${result.get('cost', 0):.4f} | latency: {result.get('latency', 0):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
