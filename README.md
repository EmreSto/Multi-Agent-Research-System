# Multi-Agent Research System

AI agents that teach you academic papers, implement methodologies, and verify each other's work. Built on the Anthropic API with a custom orchestrator.

**You don't just get answers, you get understanding.** The system teaches you the paper while building the implementation. By the end, you understand the methodology AND have working code. The pipeline stops when uncertain. Reliability > speed > comprehensiveness.

## Quick start

```bash
git clone https://github.com/EmreSto/Multi-Agent-Research-System.git
cd Multi-Agent-Research-System
pip install -r requirements.txt
cp .env.example .env
# Add your Anthropic API key to .env
python main.py
```

Requires Python 3.11+ and an [Anthropic API key](https://console.anthropic.com/).

## Usage

**Simple mode** - talk directly to an agent with `@`:
```
@teacher Teach me the attention mechanism from Vaswani et al. 2017
@mathematician Verify this loss function derivation
```

**Query mode** - let the orchestrator decide which agents to use:
```
Is my transformer implementation mathematically correct and statistically validated?
```

Multi-turn conversations supported in simple mode. Type `exit` to leave.

## Agents

| Agent | Model | What it does |
|-------|-------|-------------|
| Orchestrator | Haiku | Routes queries, validates plans, synthesizes outputs |
| Teacher | Opus | Teaches papers, briefs other agents on methodology |
| Mathematician | Opus | Verifies mathematical correctness |
| Statistician | Opus | Validates statistical methodology |
| ML Engineer | Sonnet | Implements validated formulations as code |
| Domain Expert | Sonnet | Market reality checks, economic interpretation |
| Code Optimizer | Haiku | Profiles and optimizes existing code |

Agents never talk to each other directly. All communication flows through the Orchestrator (hub-and-spoke).

## Anti-hallucination

Every claim carries a confidence tag:

- **[VERIFIED]** - supported by a quote from the paper. Flows freely
- **[HIGH CONFIDENCE]** - implied by paper context. Flows with warning
- **[RECALLED]** - from training data only. **Halts the pipeline**

6 layers implemented across source anchoring, quote-then-claim prompting, RCS relevance scoring, equation-aware chunking, verification gates, and routing validation. 4 more layers planned.

## Retrieval pipeline

Papers are chunked and stored in ChromaDB locally. On each query:

1. Chroma returns top 15 chunks by semantic similarity
2. Haiku scores each chunk 1-10 in parallel, filters below 7
3. Teacher gets compressed summaries instead of raw text (~5-8k tokens per turn instead of 25-30k)

## Build status

| Phase | Status |
|-------|--------|
| 1. Core engine (tool loop, ToolRegistry, arXiv search, anti-hallucination layers) | Done |
| 2. Paper reading (PDF parsing, quote-then-claim, orchestrator routing, RECALLED halting) | Done |
| 3. Vector DB + chunking (ChromaDB, equation-aware chunking, RCS scoring, retrieval) | Done |
| 4. Verification pipeline (rate limits, model fallback, async agents, verification gates, workflow execution) | In progress |
| 5. Interactive teaching UI (Streamlit, visualizations, code exercises) | Planned |
| 6. Polish and ship v0.1 | Planned |
| 7. v0.2 (knowledge graph, batch API, advanced verification) | Planned |

> **Note:** Some agents reference tool categories that aren't built yet (code_execution, finance, memory, visualization). This doesn't break anything. Agents work fine without them, they just won't have tool use until those categories are registered.

## License

[MIT](LICENSE)
