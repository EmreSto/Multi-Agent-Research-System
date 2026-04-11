import asyncio
import logging
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic, RateLimitError

from agents.base_agent import call_agent_async
from agents.verification_gates import (
    GateResult,
    build_code_math_verification_prompt,
    check_domain_boundary,
    check_recalled_claims,
    extract_code_blocks,
    extract_equations,
    run_all_gates,
)
from config.agent_config import MODELS
from schemas.orchestrator_schemas import StageConfig, WorkflowPlan
from tools.registry import ToolRegistry


logger = logging.getLogger(__name__)

_compress_client = AsyncAnthropic(max_retries=3, timeout=120.0)


#Input size guarding

PER_AGENT_INPUT_CHAR_CAP = 16000
HEAD_CHARS = 2000
TAIL_CHARS = 13500
TRUNCATION_MARKER = "\n[...TRUNCATED FOR COMPRESSION...]\n"
WORST_CASE_TOTAL_CAP = 40000
COMPRESSION_MAX_OUTPUT_TOKENS = 800


def _truncate_for_compression(text: str) -> str:
    if len(text) <= PER_AGENT_INPUT_CHAR_CAP:
        return text
    return text[:HEAD_CHARS] + TRUNCATION_MARKER + text[-TAIL_CHARS:]


#Compression

COMPRESS_SYSTEM_PROMPT = (
    "You are a stage-to-stage handoff compressor for a multi-agent research pipeline. "
    "You receive the outputs of one stage's agents and the task descriptions of the "
    "next stage. Produce a compressed summary of the prior outputs that is maximally "
    "useful for the next stage's agents.\n\n"
    "MANDATORY RULES:\n"
    "1. Preserve all confidence markers [VERIFIED], [HIGH_CONFIDENCE], [RECALLED] "
    "verbatim. Do not remove, paraphrase, or rewrite them.\n"
    "2. Preserve all source citations verbatim (Section X, page Y, equation labels, "
    "quoted passages). Do not summarize quoted text.\n"
    "3. Focus the summary on content relevant to the next stage's task. Drop content "
    "clearly irrelevant to the next stage.\n"
    "4. Keep the upstream agent's name attribution (e.g. 'Teacher:', 'Mathematician:') "
    "so the downstream agent knows who produced each finding.\n"
    "5. Output plain text only. No JSON, no markdown code blocks, no preamble."
)


async def compress_handoff(stage_results: list[dict], next_stage: StageConfig) -> str:
    agent_sections = []
    for r in stage_results:
        agent = r.get("agent", "unknown")
        text = r.get("text", "")
        truncated = _truncate_for_compression(text)
        agent_sections.append(f'<agent name="{agent}">\n{truncated}\n</agent>')

    payload = "\n\n".join(agent_sections)

    if len(payload) > WORST_CASE_TOTAL_CAP:
        logger.warning(
            f"compress_handoff oversized ({len(payload)} chars), skipping Haiku call"
        )
        return (
            "[COMPRESSION_SKIPPED_OVERSIZED]\n\n"
            + payload[:WORST_CASE_TOTAL_CAP]
            + "\n[...TRUNCATED AT WORST-CASE CAP...]"
        )

    next_focus = "\n".join(f"- {at.agent}: {at.task}" for at in next_stage.agents)
    user_prompt = (
        f"## Next stage tasks\n{next_focus}\n\n"
        f"## Prior stage outputs\n{payload}"
    )

    try:
        response = await _compress_client.messages.create(
            model=MODELS["haiku"],
            max_tokens=COMPRESSION_MAX_OUTPUT_TOKENS,
            system=COMPRESS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    except RateLimitError as e:
        logger.warning(f"compress_handoff hit Haiku rate limit: {e}")
        truncated_payload = payload
        if len(truncated_payload) > WORST_CASE_TOTAL_CAP:
            truncated_payload = (
                truncated_payload[:WORST_CASE_TOTAL_CAP]
                + "\n[...TRUNCATED AFTER COMPRESSION FAILURE...]"
            )
        return "[COMPRESSION_FAILED]\n\n" + truncated_payload
    except Exception as e:
        logger.error(f"compress_handoff unexpected error: {e}")
        truncated_payload = payload
        if len(truncated_payload) > WORST_CASE_TOTAL_CAP:
            truncated_payload = (
                truncated_payload[:WORST_CASE_TOTAL_CAP]
                + "\n[...TRUNCATED AFTER COMPRESSION FAILURE...]"
            )
        return "[COMPRESSION_FAILED]\n\n" + truncated_payload


#Code-math task builder

def _build_code_math_task(accumulated_results: list[dict]) -> str:
    all_text = "\n".join(r.get("text", "") for r in accumulated_results)
    equations = extract_equations(all_text)
    code_blocks = extract_code_blocks(all_text, language="python")

    if not equations and not code_blocks:
        return (
            "No equations or Python code blocks were found in prior stage outputs. "
            "Review the prior outputs directly and report any missing mathematical content."
        )
    return build_code_math_verification_prompt(equations, code_blocks)


#Default checkpoint

async def _default_checkpoint_fn(
    stage_idx: int,
    stage_results: list[dict],
    gate_summary: GateResult,
) -> bool:
    print(f"\n=== Stage {stage_idx + 1} complete ===")
    for r in stage_results:
        agent = r.get("agent", "unknown")
        text = r.get("text", "")
        print(f"\n[{agent}]")
        print(text[:1000])
        if len(text) > 1000:
            print("...")

    if gate_summary.severity == "warning":
        print(f"\nGate warnings: {gate_summary.message}")

    response = await asyncio.to_thread(
        input, f"\nApprove stage {stage_idx + 1} and continue? [y/n]: "
    )
    return response.strip().lower().startswith("y")


#System message helper

def _system_message(text: str, confidence: dict | None = None) -> dict:
    return {
        "agent": "system",
        "text": text,
        "thinking": None,
        "model": None,
        "cost": 0,
        "latency": 0,
        "history": [],
        "tool_iterations": 0,
        "confidence_summary": confidence or {
            "verified": 0,
            "high_confidence": 0,
            "recalled": 0,
            "has_recalled": False,
        },
    }


#Workflow executor

async def execute_workflow(
    workflow_plan: WorkflowPlan,
    registry: ToolRegistry,
    checkpoint_fn: Callable[[int, list[dict], GateResult], Awaitable[bool]] | None = None,
) -> list[dict]:
    if checkpoint_fn is None:
        checkpoint_fn = _default_checkpoint_fn

    results: list[dict] = []
    accumulated: list[dict] = []

    for stage_idx, stage in enumerate(workflow_plan.stages):
        tasks = [
            call_agent_async(at.agent, at.task, registry=registry)
            for at in stage.agents
        ]
        stage_results = await asyncio.gather(*tasks)

        stage_gate_results = [
            run_all_gates(
                check_recalled_claims(r),
                check_domain_boundary(r),
            )
            for r in stage_results
        ]
        stage_summary = run_all_gates(*stage_gate_results)

        results.extend(stage_results)
        accumulated.extend(stage_results)

        if not stage_summary.passed:
            results.append(_system_message(
                f"Workflow halted at stage {stage_idx + 1}: {stage_summary.message}"
            ))
            return results

        approved = await checkpoint_fn(stage_idx, stage_results, stage_summary)
        if not approved:
            results.append(_system_message(
                f"Workflow halted at stage {stage_idx + 1}: user did not approve"
            ))
            return results

        if stage_idx + 1 < len(workflow_plan.stages):
            next_stage = workflow_plan.stages[stage_idx + 1]
            if next_stage.stage_type == "code_math_verification":
                code_math_task = _build_code_math_task(accumulated)
                for at in next_stage.agents:
                    at.task = code_math_task
            elif stage.pass_forward:
                compressed = await compress_handoff(stage_results, next_stage)
                for at in next_stage.agents:
                    at.task = (
                        at.task
                        + "\n\n<upstream_output>\n"
                        + compressed
                        + "\n</upstream_output>"
                    )

    return results
