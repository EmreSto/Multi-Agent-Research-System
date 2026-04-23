# Multi-Agent Research System

Read deep learning papers with AI agents that ground every claim in the source, halt when uncertain, and cite by section and page.

Built on the Anthropic API. No external agent frameworks.

## Quick start

```bash
git clone https://github.com/EmreSto/Multi-Agent-Research-System.git
cd Multi-Agent-Research-System
pip install -r requirements.txt
cp .env.example .env
# paste your Anthropic API key into .env
python main.py
```

Requires Python 3.11 or newer and an [Anthropic API key](https://console.anthropic.com/).

## Usage

Three modes. The Orchestrator picks one for you, or you can force simple mode by addressing an agent directly.

**Simple.** Talk to a specific agent.

```
@teacher Explain scaled dot-product attention from the Transformer paper.
@mathematician Verify this gradient derivation.
```

**Routing.** Two or three agents in sequence, one pass.

```
Derive the backward pass of layer normalization and implement it in PyTorch.
```

**Workflow.** Multi-stage with a user checkpoint between each stage.

```
Teach me the Adam optimizer and implement it from scratch.
```

Type `exit` to leave. Multi-turn conversations are supported in simple mode.

## Agents

| Agent | Model | Role |
|---|---|---|
| orchestrator | Haiku | Routes queries, emits structured plans via tool calls |
| teacher | Opus | Reads papers, teaches the user, briefs other agents |
| mathematician | Opus | Verifies derivations, gradient flow, attention math |
| ml_engineer | Sonnet | Translates verified formulations into code |

Agents never talk to each other directly. All communication flows through the Orchestrator (hub and spoke).

## Anti-hallucination

Every substantive claim carries a confidence tag:

- `[VERIFIED]` direct quote from a retrieved chunk. Flows freely.
- `[HIGH_CONFIDENCE]` implied by retrieved chunks but no exact quote. Flows with a warning.
- `[RECALLED]` from training knowledge only, not grounded in any retrieved source. Halts the pipeline.

Six layers implemented: source anchoring via Pydantic schemas, quote-then-claim prompting, RCS relevance scoring, equation-aware chunking with page numbers, cross-agent verification gates, routing validation.

## Retrieval pipeline

Papers are chunked by section (equation-aware, page numbers preserved) and stored in ChromaDB locally. On every query:

1. Chroma returns the top 15 chunks by semantic similarity.
2. Haiku scores each chunk 0 to 10 via a structured `emit_score` tool call. Chunks below 7 are dropped.
3. Every surviving chunk keeps its `raw_text`. Teacher quotes `raw_text` for `[VERIFIED]` claims.
4. Chunks are reordered so the highest scored land at the start and end of context.

Rate limiting: Haiku scoring runs in a thread pool with a shared throttle (40 RPM ceiling) and exponential-backoff retry on 429.

## Evals

A small gold-labeled harness lives in `evals/`.

```bash
python -m evals.runner <run_name>
python -m evals.metrics evals/results/<run_name>.json
python -m evals.metrics --diff evals/results/<baseline>.json evals/results/<current>.json
```

Measures grounding precision, retrieval recall at 7, trap halt rate, and false VERIFIED rate against gold claims in `evals/papers/`. The `--diff` form exits non zero when a monotone up metric drops more than one percentage point or the false VERIFIED rate rises by more than one point.

A RECALLED halt regression test lives at `evals/regression_recalled.py`.

## License

[MIT](LICENSE)
