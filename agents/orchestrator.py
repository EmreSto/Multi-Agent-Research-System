import re

from anthropic import Anthropic
from dotenv import load_dotenv
from agents.base_agent import call_agent, load_skill
from schemas.orchestrator_schemas import RoutingPlan
from config.agent_config import AGENT_CONFIG


load_dotenv()

client = Anthropic()

def get_routing_plan(user_message):
    skill = load_skill("orchestrator")
    agent_config = AGENT_CONFIG["orchestrator"]

    response = client.messages.parse(
        model=agent_config["model"],
        max_tokens=agent_config["max_tokens"],
        system=[
            {
            "type": "text",
            "text": skill,
            "cache_control":{"type":"ephemeral"}
            }
        ],
        messages=[
            {"role": "user", "content": user_message}
        ],
        output_format=RoutingPlan,
)
    return response.parsed_output

def check_simple_mode(user_message):
    if user_message.startswith("@"):
        first_word = user_message.split()[0]
        agent_name = first_word[1:]
        if agent_name in AGENT_CONFIG:
            return " ".join(user_message.split()[1:]), agent_name
        else:
            return None, None
    else:
        return None, None

def should_stop_pipeline(call_result):
    summary = call_result.get("confidence_summary", {})
    if summary.get("has_recalled", False):
        return True

    # Regex fallback
    text = call_result.get("text", "")
    if re.search(r"\[RECALLED\b|RECALLED\s*[—–-]", text, re.IGNORECASE):
        return True

    return False

def validate_routing_plan(routing_plan) -> tuple[bool, str]:
    valid_agents = set(AGENT_CONFIG.keys()) - {"orchestrator"}

    for agent in routing_plan.agents:
        if agent not in valid_agents:
            return False, (
                f"Unknown agent '{agent}' in routing plan. "
                f"Valid agents: {sorted(valid_agents)}"
            )

    agents = routing_plan.agents
    if "mathematician" in agents and "statistician" in agents:
        if agents.index("mathematician") > agents.index("statistician"):
            return False, (
                "Routing rule violation: mathematician must run before statistician."
            )

    if "code_optimizer" in agents and len(agents) == 1:
        return False, (
            "Routing rule violation: code_optimizer cannot run alone — "
            "must be paired with at least one domain agent."
        )

    return True, ""


def execute_query(user_message, registry=None):
    query, agent_name = check_simple_mode(user_message)
    if agent_name:
        return call_agent(agent_name, query, registry=registry)

    routing_plan = get_routing_plan(user_message)

    valid, error_msg = validate_routing_plan(routing_plan)
    if not valid:
        return [{
            "agent": "system",
            "text": f"Routing validation failed: {error_msg}",
            "thinking": None,
            "model": None,
            "cost": 0,
            "latency": 0,
            "history": [],
            "tool_iterations": 0,
            "confidence_summary": {"verified": 0, "high_confidence": 0, "recalled": 0, "has_recalled": False},
        }]

    results = []
    for agent in routing_plan.agents:
        call_result = call_agent(agent, user_message, registry=registry)
        results.append(call_result)

        if should_stop_pipeline(call_result):
            confidence = call_result.get("confidence_summary", {})
            results.append({
                "agent": "system",
                "text": (
                    f"Pipeline halted: {call_result['agent']} returned RECALLED claims "
                    f"that need source verification before proceeding. "
                    f"Confidence breakdown: {confidence}"
                ),
                "thinking": None,
                "model": None,
                "cost": 0,
                "latency": 0,
                "history": [],
                "tool_iterations": 0,
                "confidence_summary": confidence,
            })
            break

        if "[NOT MY DOMAIN]" in call_result.get("text", ""):
            results.append({
                "agent": "system",
                "text": (
                    f"Agent '{agent}' rejected this query as outside its domain. "
                    f"Response: {call_result['text'][:500]}"
                ),
                "thinking": None,
                "model": None,
                "cost": 0,
                "latency": 0,
                "history": [],
                "tool_iterations": 0,
                "confidence_summary": {"verified": 0, "high_confidence": 0, "recalled": 0, "has_recalled": False},
            })
            break

        if routing_plan.pass_forward:
            confidence = call_result.get("confidence_summary", {})
            warning = ""
            if confidence.get("high_confidence", 0) > 0:
                warning = (
                    f"\n<confidence_warning>Upstream output contains "
                    f"{confidence['high_confidence']} HIGH_CONFIDENCE claim(s) "
                    f"— not fully verified against source.</confidence_warning>\n"
                )
            user_message = (
                user_message + "\n\n<previous_agent_output>"
                + warning + "\n" + call_result["text"]
                + "\n</previous_agent_output>"
            )
    return results


