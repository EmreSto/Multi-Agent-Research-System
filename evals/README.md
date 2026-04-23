# Evals harness

Minimal harness for measuring ResearchCrew's grounding precision, retrieval recall, and RECALLED-trap behavior across runs.

## Layout

```
evals/
  papers/<paper_id>.json    # gold claims + RECALLED-trap queries per paper
  runner.py                 # runs Teacher against every gold item; saves raw outputs
  metrics.py                # computes metrics from runner output; diffs two runs
  results/                  # runner output lands here (gitignored)
```

## Run a full pass

```bash
python -m evals.runner block-a-baseline
python -m evals.metrics evals/results/block-a-baseline.json
```

## Compare two runs (regression gate)

```bash
python -m evals.metrics --diff evals/results/block-a-baseline.json evals/results/block-b-end.json
```

Exit code 0 = no regression. Exit code 2 = at least one monotone-up metric dropped by more than 1 percentage point, or false-VERIFIED rate increased by more than 1 point.

## Adding a paper

1. Put the PDF in `sources/`.
2. Create `evals/papers/<paper_id>.json` mirroring `attention.json`. 15 claims + 5 traps is the target.
3. `gold_quote_fragments` are case-insensitive substrings that must all appear in Teacher's output (permissive - we're testing for evidence of quoting the source, not exact match).
4. `gold_section` is matched as a case-insensitive substring against a retrieved chunk's `section_name`.

## Metrics

| Metric | Meaning |
|---|---|
| Grounding precision | Claims where Teacher's output both labels correctly AND contains every gold quote fragment |
| Label accuracy | Claims where the confidence tag matches `expected_confidence` |
| Quote present | Claims where every `gold_quote_fragments` substring appears in the output |
| Retrieval recall@7 | Claims where a chunk from `gold_section` appears in the top 7 RCS-filtered chunks |
| Trap halt rate | Trap queries where Teacher output contains a `[RECALLED]` tag |
| False VERIFIED rate | Trap queries where Teacher output contains a `[VERIFIED]` tag (should be 0) |
