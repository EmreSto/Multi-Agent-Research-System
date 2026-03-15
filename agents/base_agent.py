import json , time , datetime 
from dotenv import load_dotenv
from pathlib import Path
from anthropic import Anthropic
from config.agent_config import MODELS, PRICING, AGENT_CONFIG

PROJECT_ROOT = Path(__file__).parent.parent 
skills_path = PROJECT_ROOT / "skills" 
logs_path = PROJECT_ROOT / "logs" 
logs_path.mkdir(exist_ok=True)
context_path = PROJECT_ROOT / "context"


load_dotenv()

client = Anthropic()

# Loader functions
def load_skill(agent_name):
    skill_file = skills_path / f"{agent_name}_skill.md"
    if skill_file.exists():
       return skill_file.read_text()
    else:
       return f"You are the {agent_name} agent." 
def load_context():
   context_file = context_path / "context.md"
   if context_file.exists():
      return context_file.read_text()
   else:
      return ""
def load_scratchpad(agent_name):
   scratchpad_file = context_path / "scratchpads" / f"{agent_name}.md"
   if scratchpad_file.exists():
      return scratchpad_file.read_text()
   else:
      return ""
###

def calculate_cost(model_name, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens):
   million_tokens = 1000000
   pricing = PRICING[model_name]
   
   regular_input = input_tokens - cache_creation_input_tokens - cache_read_input_tokens
   input_cost = (regular_input / million_tokens) * pricing["input"]
   output_cost = (output_tokens / million_tokens) * pricing["output"]
   cache_write_cost = (cache_creation_input_tokens / million_tokens) * pricing["cache_write"]
   cache_read_cost = (cache_read_input_tokens / million_tokens) * pricing["cache_read"]

   total_cost = input_cost + output_cost + cache_write_cost + cache_read_cost

   return total_cost, input_cost, output_cost, cache_write_cost, cache_read_cost


def log_call(agent_name, model,user_message, response_text, thinking_text, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_cost , latency):
   log_entry = {
    "timestamp": datetime.datetime.now().isoformat(),
    "agent": agent_name,
    "model": model,
    "user_message": user_message,
    "response": response_text,
    "thinking": thinking_text,
    "input_tokens": input_tokens,
    "output_tokens": output_tokens,
    "cache_creation_input_tokens": cache_creation_input_tokens,
    "cache_read_input_tokens": cache_read_input_tokens,
    "total_cost": total_cost,
    "latency": latency,
   }
   log_file = logs_path / f"{datetime.date.today()}_session.jsonl"
   with open(log_file, "a") as f:
      f.write(json.dumps(log_entry) + "\n")




def call_agent(agent_name, user_message):
   config = AGENT_CONFIG[agent_name]
   skill = load_skill(agent_name)
   context = load_context()
   scratchpad = load_scratchpad(agent_name)

   parts = []
   if context:
      parts.append(f"<context>\n{context}\n</context>")
   if scratchpad:
      parts.append(f"<scratchpad>\n{scratchpad}\n</scratchpad>")
   parts.append(f"<task>\n{user_message}\n</task>")
   full_message = "\n\n".join(parts)

   api_kwargs = {
      "model":config["model"],
      "max_tokens":config["max_tokens"],
      "system":[
         {
            "type":"text",
            "text": skill,
            "cache_control": {"type": "ephemeral"}
         }
      ],
      "messages":[
         {"role": "user", "content" : full_message}
      ],
   }
   if config["thinking"]:
      api_kwargs["thinking"] = config["thinking"]

   start_time = time.time()

   response = client.messages.create(**api_kwargs)


   latency = time.time() - start_time 


   response_text = ""
   thinking_text = ""

   for block in response.content:
      if block.type == "thinking":
         thinking_text += block.thinking
      elif block.type == "text":
         response_text += block.text
   
   input_tokens = response.usage.input_tokens
   output_tokens = response.usage.output_tokens
   cache_creation_input_tokens = getattr(response.usage, "cache_creation_input_tokens", 0)
   cache_read_input_tokens = getattr(response.usage, "cache_read_input_tokens", 0)

   total_cost, _, _, _, _ = calculate_cost(
       config["model"], input_tokens, output_tokens,
       cache_creation_input_tokens, cache_read_input_tokens
   )

   log_call(
       agent_name, config["model"], user_message, response_text,
       thinking_text, input_tokens, output_tokens,
       cache_creation_input_tokens, cache_read_input_tokens,
       total_cost, latency
   )

   return {
       "text": response_text,
       "thinking": thinking_text,
       "model": config["model"],
       "cost": total_cost,
       "latency": latency,
   }




