# Project terminology

Multi-agent research framework for teaching and implementing deep learning
papers. The user values reliability over speed and wants grounded answers,
not training-memory summaries.

## Instructions for agents with research tools

If you have `list_sources`, `ingest_paper`, `retrieve_chunks`, or `parse_pdf`
in your tool list, these rules apply:

1. **Call `list_sources` FIRST** before answering any substantive question
   about a paper, method, or technical concept. Check what's in `sources/`.
2. If the user's query matches a paper in `sources/` even implicitly
   (e.g. "teach me attention mechanisms" and `1706.03762v7.pdf` is present),
   use that paper. Do not require the user to name the file.
3. Prefer `retrieve_chunks` over `parse_pdf` for long papers. It stays
   within rate limits and returns the full raw text of the most relevant
   chunks (every chunk with relevance score >= 7 keeps its raw_text).
4. If a paper has not been ingested yet, call `ingest_paper` once, then use
   `retrieve_chunks`.
5. Ground every substantive claim in a quoted passage from `raw_text`. Mark
   verified claims as `[VERIFIED]`. Mark training-knowledge claims as
   `[RECALLED]`.
6. **Do not rationalize [RECALLED] claims** as "textbook", "widely known",
   or "foundational". The confidence system is structural - it distinguishes
   paper-grounded from memory-based, and popularity does not convert one
   into the other. If a claim matters and you do not have a quote for it,
   either find a quote or accept the RECALLED label.

## Confidence levels the gate system cares about

- `[VERIFIED]` - direct quote from a source currently in context
- `[HIGH_CONFIDENCE]` - partial source access, interpretation flagged
- `[RECALLED]` - training knowledge only, not grounded in any source

Absence is `[RECALLED]`, not a creative label like "VERIFIED by absence" or
"VERIFIED via negative result". If the paper does not say X, the claim that
it does not say X is still a claim about what the paper does not contain -
which you cannot verify from training knowledge. Use `[RECALLED]`.

The workflow gate allows mostly-verified output through as a warning (ratio
of at least 3 VERIFIED per 1 RECALLED). Pure-RECALLED output hard-halts the
pipeline. A high verified count is your goal, not a low recalled count.

## Agent roster (v0.1)

- **orchestrator** - routes queries, emits plans via `emit_simple_plan`,
  `emit_routing_plan`, or `emit_workflow_plan` tools.
- **teacher** - teaches deep learning papers, briefs other agents on
  methodology. Owns retrieval tools.
- **mathematician** - verifies mathematical correctness (matrix calculus,
  gradient flow, attention derivations, optimization).
- **ml_engineer** - implements validated formulations as code.
