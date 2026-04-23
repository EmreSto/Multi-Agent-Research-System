"""Metrics over evals/runner.py output.

Usage:
    python -m evals.metrics <results_file.json>
    python -m evals.metrics --diff <baseline.json> <current.json>
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"

REGRESSION_TOLERANCE_PCT = 1.0


def count_confidence_markers(text: str) -> dict:
    verified = len(re.findall(r"\[VERIFIED\b[^\]]*\]", text, re.IGNORECASE))
    high_conf = len(re.findall(r"\[HIGH_CONFIDENCE\b[^\]]*\]", text, re.IGNORECASE))
    recalled = len(re.findall(r"\[RECALLED\b[^\]]*\]", text, re.IGNORECASE))
    return {"verified": verified, "high_confidence": high_conf, "recalled": recalled}


def claim_quotes_source(teacher_text: str, gold_fragments: list[str]) -> bool:
    if not gold_fragments:
        return False
    text_lower = teacher_text.lower()
    return all(frag.lower() in text_lower for frag in gold_fragments)


def claim_label_matches(teacher_text: str, expected: str) -> bool:
    markers = count_confidence_markers(teacher_text)
    expected_upper = expected.upper()
    if expected_upper == "VERIFIED":
        return markers["verified"] > 0
    if expected_upper == "HIGH_CONFIDENCE":
        return markers["high_confidence"] > 0
    if expected_upper == "RECALLED":
        return markers["recalled"] > 0
    return False


def retrieval_recall_at_k(retrieval: dict, gold_section: str, k: int = 7) -> bool:
    if not gold_section or retrieval.get("status") != "success":
        return False
    chunks = (
        retrieval.get("ordered_chunks")
        or retrieval.get("ordered_summaries")
        or []
    )[:k]
    needle = gold_section.lower()
    return any(
        needle in (chunk.get("section_name", "") or "").lower()
        for chunk in chunks
    )


def _pct(num: int, denom: int) -> float | None:
    if denom == 0:
        return None
    return round(num / denom * 100, 1)


def compute_metrics(results_path: Path) -> dict:
    data = json.loads(results_path.read_text())

    total_claims = 0
    label_correct = 0
    quote_present = 0
    grounded = 0
    retrieval_hits = 0

    total_traps = 0
    false_verified = 0
    halted = 0

    per_paper: dict[str, dict] = {}

    for paper in data.get("papers", []):
        paper_id = paper["paper_id"]
        p_stats = {
            "claims_total": 0,
            "label_correct": 0,
            "quote_present": 0,
            "grounded": 0,
            "retrieval_at_7_hits": 0,
            "traps_total": 0,
            "halted": 0,
            "false_verified": 0,
        }

        for cr in paper.get("claim_results", []):
            gold = cr.get("gold", {})
            teacher_text = (cr.get("teacher", {}) or {}).get("text", "") or ""
            retrieval = cr.get("retrieval", {})

            label_ok = claim_label_matches(teacher_text, gold.get("expected_confidence", ""))
            quote_ok = claim_quotes_source(teacher_text, gold.get("gold_quote_fragments", []))
            retrieval_ok = retrieval_recall_at_k(retrieval, gold.get("gold_section", ""), k=7)

            total_claims += 1
            p_stats["claims_total"] += 1
            label_correct += int(label_ok)
            quote_present += int(quote_ok)
            retrieval_hits += int(retrieval_ok)
            grounded += int(label_ok and quote_ok)
            p_stats["label_correct"] += int(label_ok)
            p_stats["quote_present"] += int(quote_ok)
            p_stats["retrieval_at_7_hits"] += int(retrieval_ok)
            p_stats["grounded"] += int(label_ok and quote_ok)

        for tr in paper.get("trap_results", []):
            teacher_text = (tr.get("teacher", {}) or {}).get("text", "") or ""
            markers = count_confidence_markers(teacher_text)

            total_traps += 1
            p_stats["traps_total"] += 1
            if markers["recalled"] > 0:
                halted += 1
                p_stats["halted"] += 1
            if markers["verified"] > 0:
                false_verified += 1
                p_stats["false_verified"] += 1

        per_paper[paper_id] = p_stats

    overall = {
        "total_claims": total_claims,
        "grounding_precision_pct": _pct(grounded, total_claims),
        "label_accuracy_pct": _pct(label_correct, total_claims),
        "quote_present_pct": _pct(quote_present, total_claims),
        "retrieval_recall_at_7_pct": _pct(retrieval_hits, total_claims),
        "total_traps": total_traps,
        "trap_halt_rate_pct": _pct(halted, total_traps),
        "false_verified_rate_pct": _pct(false_verified, total_traps),
    }

    return {
        "source_file": str(results_path),
        "overall": overall,
        "per_paper": per_paper,
    }


def print_report(metrics: dict) -> None:
    print("=" * 60)
    print("EVALS METRICS")
    print("=" * 60)
    ov = metrics["overall"]
    print(f"\nSource: {metrics['source_file']}")
    print(f"\nClaims:  {ov['total_claims']}")
    print(f"  Grounding precision (label AND quote ok):  {ov['grounding_precision_pct']}%")
    print(f"  Label accuracy:                            {ov['label_accuracy_pct']}%")
    print(f"  Gold quote present in output:              {ov['quote_present_pct']}%")
    print(f"  Retrieval recall@7:                        {ov['retrieval_recall_at_7_pct']}%")
    print(f"\nTraps:   {ov['total_traps']}")
    print(f"  Halted (RECALLED tagged):                  {ov['trap_halt_rate_pct']}%")
    print(f"  False VERIFIED on trap:                    {ov['false_verified_rate_pct']}%")
    print("\nPer paper:")
    for pid, stats in metrics["per_paper"].items():
        print(f"  {pid}: {stats}")


def diff_reports(baseline_path: Path, current_path: Path) -> dict:
    baseline = compute_metrics(baseline_path)["overall"]
    current = compute_metrics(current_path)["overall"]

    regressions: dict[str, dict] = {}

    monotone_up = [
        "grounding_precision_pct",
        "label_accuracy_pct",
        "quote_present_pct",
        "retrieval_recall_at_7_pct",
        "trap_halt_rate_pct",
    ]
    for key in monotone_up:
        b, c = baseline.get(key), current.get(key)
        if b is None or c is None:
            continue
        if c < b - REGRESSION_TOLERANCE_PCT:
            regressions[key] = {"baseline": b, "current": c, "delta": round(c - b, 1)}

    b_fv, c_fv = baseline.get("false_verified_rate_pct"), current.get("false_verified_rate_pct")
    if b_fv is not None and c_fv is not None and c_fv > b_fv + REGRESSION_TOLERANCE_PCT:
        regressions["false_verified_rate_pct"] = {
            "baseline": b_fv,
            "current": c_fv,
            "delta": round(c_fv - b_fv, 1),
        }

    return {
        "baseline": baseline,
        "current": current,
        "regressions": regressions,
        "passed": len(regressions) == 0,
    }


def _usage() -> None:
    print(__doc__)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        _usage()
        sys.exit(1)

    if args[0] == "--diff":
        if len(args) != 3:
            _usage()
            sys.exit(1)
        diff = diff_reports(Path(args[1]), Path(args[2]))
        print(json.dumps(diff, indent=2))
        sys.exit(0 if diff["passed"] else 2)

    results_path = Path(args[0])
    if not results_path.exists():
        alt = RESULTS_DIR / args[0]
        if alt.exists():
            results_path = alt
        else:
            print(f"results file not found: {args[0]}", file=sys.stderr)
            sys.exit(1)

    metrics = compute_metrics(results_path)
    print_report(metrics)
