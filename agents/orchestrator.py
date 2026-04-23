from pydantic import TypeAdapter

from agents.base_agent import call_agent_async, load_skill
from agents.verification_gates import (
    check_domain_boundary,
    check_recalled_claims,
    run_all_gates,
    validate_routing_plan,
    validate_upstream_reference,
)
from config.agent_config import AGENT_CONFIG
from config.clients import async_client
from schemas.orchestrator_schemas import (
    OrchestratorPlan,
    RoutingPlan,
    RoutingPlanSimple,
    WorkflowPlan,
)


_plan_adapter = TypeAdapter(OrchestratorPlan)


EMIT_SIMPLE_PLAN_TOOL = {
    "name": "emit_simple_plan",
    "description": (
        "Emit a simple plan: route the query to exactly one specialist agent "
        "with no downstream dependencies. Use when one agent can fully answer "
        "(e.g. '@mathematician verify this proof', or 'teach me attention from "
        "this paper' where Teacher alone suffices)."
    ),
    "input_schema": RoutingPlanSimple.model_json_schema(),
}

EMIT_ROUTING_PLAN_TOOL = {
    "name": "emit_routing_plan",
    "description": (
        "Emit a routing plan: 2-3 agents run in sequence and the query "
        "completes in one pass with no user checkpoint between them. Use for "
        "queries like 'Derive this attention formula and implement it' "
        "(mathematician -> ml_engineer) or 'Teach me this method then code it' "
        "(teacher -> ml_engineer)."
    ),
    "input_schema": RoutingPlan.model_json_schema(),
}

EMIT_WORKFLOW_PLAN_TOOL = {
    "name": "emit_workflow_plan",
    "description": (
        "Emit a workflow plan: multiple stages with a user checkpoint between "
        "each. Within a stage, agents may run in parallel. Use for multi-phase "
        "requests like 'Teach me this paper and then implement the methodology.'"
    ),
    "input_schema": WorkflowPlan.model_json_schema(),
}

PLAN_TOOLS = [
    EMIT_SIMPLE_PLAN_TOOL,
    EMIT_ROUTING_PLAN_TOOL,
    EMIT_WORKFLOW_PLAN_TOOL,
]


async def get_plan(user_message):
    skill = load_skill("orchestrator")
    agent_config = AGENT_CONFIG["orchestrator"]

    response = await async_client.messages.create(
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
        tools=PLAN_TOOLS,
        tool_choice={"type": "any"},
    )

    tool_use_block = next(
        (b for b in response.content if b.type == "tool_use"),
        None,
    )
    if tool_use_block is None:
        raise RuntimeError(
            "orchestrator did not emit a plan tool_use block; "
            f"stop_reason={response.stop_reason}"
        )
    return _plan_adapter.validate_python(tool_use_block.input)


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


async def _execute_routing(plan, user_message, registry):
    plan_gate = validate_routing_plan(plan)
    if not plan_gate.passed:
        return [_system_message(f"Routing validation failed: {plan_gate.message}")]

    results = []
    prev_text = None
    for agent in plan.agents:
        call_result = await call_agent_async(agent, user_message, registry=registry)
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


async def execute_query(user_message, registry=None, checkpoint_fn=None):
    query, agent_name = check_simple_mode(user_message)
    if agent_name:
        return await call_agent_async(agent_name, query, registry=registry)

    plan = await get_plan(user_message)

    if plan.mode == "simple":
        return await call_agent_async(plan.agent, user_message, registry=registry)
    if plan.mode == "routing":
        return await _execute_routing(plan, user_message, registry)
    if plan.mode == "workflow":
        from agents.workflow_executor import execute_workflow
        return await execute_workflow(plan, registry, checkpoint_fn)

    return [_system_message(f"Unknown plan mode: {plan.mode}")]
