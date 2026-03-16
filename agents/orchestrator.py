from anthropic import Anthropic
from dotenv import load_dotenv
from agents.base_agent import call_agent, load_skill
from schemas.orchestrator_schemas import RoutingPlan, RoutingPlanSimple
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
        

def execute_query(user_message):
    query, agent_name = check_simple_mode(user_message)
    if agent_name:
        return call_agent(agent_name, query)
    
    routing_plan = get_routing_plan(user_message)
    results = []
    for agent in routing_plan.agents:
        call_result = call_agent(agent ,user_message)
        results.append(call_result)
        if routing_plan.pass_forward:
            user_message = user_message + "\n\n<previous_agent_output>\n" + call_result["text"] + "\n</previous_agent_output>"
    return results



