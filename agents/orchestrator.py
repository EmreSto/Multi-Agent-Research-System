import asyncio
import json
import re

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import TypeAdapter

from agents.base_agent import call_agent, load_skill
from agents.verification_gates import (
    check_domain_boundary,
    check_recalled_claims,
    run_all_gates,
    validate_routing_plan,
    validate_upstream_reference,
)
from config.agent_config import AGENT_CONFIG
from schemas.orchestrator_schemas import OrchestratorPlan


load_dotenv()

client = Anthropic(max_retries=3, timeout=120.0)

_plan_adapter = TypeAdapter(OrchestratorPlan)


def _extract_json(text: str) -> dict:
    text = text.strip()

    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start != -1:
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(text[start:])
            return obj
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("no JSON object found in orchestrator response", text, 0)


def get_plan(user_message):
    skill = load_skill("orchestrator")
    agent_config = AGENT_CONFIG["orchestrator"]

    response = client.messages.create(
        model=agent_config["model"],
        max_tokens=agent_config["max_tokens"],
        system=[
            {
                "type": "text",
                "text": skill,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": user_message}
        ],
    )

    raw_text = response.content[0].text
    plan_json = _extract_json(raw_text)
    return _plan_adapter.validate_python(plan_json)


def check_simple_mode(user_message):
    if user_message.startswith("@"):
        first_word = user_message.split()[0]
        agent_name = first_word[1:]
        if agent_name in AGENT_CONFIG:
            return " ".join(user_message.split()[1:]), agent_name
        return None, None
    return None, None


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


def _execute_routing(plan, user_message, registry):
    plan_gate = validate_routing_plan(plan)
    if not plan_gate.passed:
        return [_system_message(f"Routing validation failed: {plan_gate.message}")]

    results = []
    prev_text = None
    for agent in plan.agents:
        call_result = call_agent(agent, user_message, registry=registry)
        results.append(call_result)

        gates = [
            check_recalled_claims(call_result),
            check_domain_boundary(call_result),
        ]
        if prev_text is not None:
            gates.append(
                validate_upstream_reference(call_result.get("text", ""), prev_text)
            )

        stage_gate = run_all_gates(*gates)

        if not stage_gate.passed:
            results.append(_system_message(
                f"Pipeline halted: {stage_gate.message}",
                confidence=call_result.get("confidence_summary", {}),
            ))
            break

        if stage_gate.severity == "warning":
            results.append(_system_message(f"Gate warning: {stage_gate.message}"))

        if plan.pass_forward:
            confidence = call_result.get("confidence_summary", {})
            warning = ""
            if confidence.get("high_confidence", 0) > 0:
                warning = (
                    f"\n<confidence_warning>Upstream output contains "
                    f"{confidence['high_confidence']} HIGH_CONFIDENCE claim(s) — "
                    f"not fully verified against source.</confidence_warning>\n"
                )
            user_message = (
                user_message
                + "\n\n<upstream_output>"
                + warning
                + "\n"
                + call_result["text"]
                + "\n</upstream_output>"
            )
            prev_text = call_result["text"]

    return results


def execute_query(user_message, registry=None, checkpoint_fn=None):
    query, agent_name = check_simple_mode(user_message)
    if agent_name:
        return call_agent(agent_name, query, registry=registry)

    plan = get_plan(user_message)

    if plan.mode == "simple":
        return call_agent(plan.agent, user_message, registry=registry)
    if plan.mode == "routing":
        return _execute_routing(plan, user_message, registry)
    if plan.mode == "workflow":
        from agents.workflow_executor import execute_workflow
        return asyncio.run(execute_workflow(plan, registry, checkpoint_fn))

    return [_system_message(f"Unknown plan mode: {plan.mode}")]
