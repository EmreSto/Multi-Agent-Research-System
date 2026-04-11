import asyncio
import json
import logging
import re
import time
import datetime
from dotenv import load_dotenv
from pathlib import Path
from anthropic import Anthropic, AsyncAnthropic, RateLimitError
from config.agent_config import PRICING, AGENT_CONFIG
from config.rate_limits import get_rate_limit_state, get_fallback_model, get_rate_limit_lock

logger = logging.getLogger(__name__)

#Paths
PROJECT_ROOT = Path(__file__).parent.parent
skills_path = PROJECT_ROOT / "skills"
logs_path = PROJECT_ROOT / "logs"
logs_path.mkdir(exist_ok=True)
context_path = PROJECT_ROOT / "context"

load_dotenv()
client = Anthropic(max_retries=3, timeout=120.0)
async_client = AsyncAnthropic(max_retries=3, timeout=120.0)


#Loaders

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
#Cost calculation

def calculate_cost(model_name, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens):
   million_tokens = 1000000
   pricing = PRICING[model_name]

   input_cost = (input_tokens / million_tokens) * pricing["input"]
   output_cost = (output_tokens / million_tokens) * pricing["output"]
   cache_write_cost = (cache_creation_input_tokens / million_tokens) * pricing["cache_write"]
   cache_read_cost = (cache_read_input_tokens / million_tokens) * pricing["cache_read"]

   total_cost = input_cost + output_cost + cache_write_cost + cache_read_cost

   return total_cost, input_cost, output_cost, cache_write_cost, cache_read_cost


#Logging

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




#Confidence scanning

def scan_confidence_markers(text: str) -> dict:
   verified = len(re.findall(
      r"\[VERIFIED\b[^\]]*\]", text, re.IGNORECASE
   ))
   high_confidence = len(re.findall(
      r"\[HIGH.CONFIDENCE\b[^\]]*\]|HIGH.CONFIDENCE\s*[—–-]", text, re.IGNORECASE
   ))
   recalled = len(re.findall(
      r"\[RECALLED\b[^\]]*\]|RECALLED\s*[—–-]", text, re.IGNORECASE
   ))
   return {
      "verified": verified,
      "high_confidence": high_confidence,
      "recalled": recalled,
      "has_recalled": recalled > 0,
   }


#Serialization

def _serialize_content(content_blocks):
   result = []
   for block in content_blocks:
      if block.type == "thinking":
         entry = {"type": "thinking", "thinking": block.thinking}
         if hasattr(block, "signature") and block.signature:
            entry["signature"] = block.signature
         result.append(entry)
      elif block.type == "text":
         result.append({"type": "text", "text": block.text})
      elif block.type == "tool_use":
         result.append({
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
         })
   return result

def _make_api_call(api_kwargs: dict) -> tuple:
   rate_limit_state_model = get_rate_limit_state(api_kwargs["model"])
   if rate_limit_state_model.should_pause():
      time.sleep(rate_limit_state_model.pause_duration())
   try:
      raw = client.messages.with_raw_response.create(**api_kwargs)
      rate_limit_state_model.update_from_headers(raw.headers)
      parsed_response = raw.parse()
      return (parsed_response, api_kwargs["model"])
   except RateLimitError:
      fall_back_model = get_fallback_model(api_kwargs["model"])
      if fall_back_model is None:
         raise
      api_kwargs["model"] = fall_back_model
      fallback_raw = client.messages.with_raw_response.create(**api_kwargs)
      get_rate_limit_state(fall_back_model).update_from_headers(fallback_raw.headers)
      fallback_response = fallback_raw.parse()
      return(fallback_response, fall_back_model )

async def _make_api_call_async(api_kwargs: dict) -> tuple:
   model_name = api_kwargs["model"]
   async with get_rate_limit_lock(model_name):
      rate_limit_state_model = get_rate_limit_state(model_name)
      if rate_limit_state_model.should_pause():
         await asyncio.sleep(rate_limit_state_model.pause_duration())
   try:
      raw = await async_client.messages.with_raw_response.create(**api_kwargs)
      async with get_rate_limit_lock(model_name):
         rate_limit_state_model.update_from_headers(raw.headers)
      parsed_response = raw.parse()
      return (parsed_response, model_name)
   except RateLimitError:
      fall_back_model = get_fallback_model(model_name)
      if fall_back_model is None:
         raise
      api_kwargs["model"] = fall_back_model
      fallback_raw = await async_client.messages.with_raw_response.create(**api_kwargs)
      async with get_rate_limit_lock(fall_back_model):
         get_rate_limit_state(fall_back_model).update_from_headers(fallback_raw.headers)
      fallback_response = fallback_raw.parse()
      return (fallback_response, fall_back_model)


#Agent call

def call_agent(agent_name, user_message, history=None, registry=None):
   if history is None:
      history = []
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

   messages = history + [{"role": "user", "content": full_message}]

   api_kwargs = {
      "model": config["model"],
      "max_tokens": config["max_tokens"],
      "system": [
         {
            "type": "text",
            "text": skill,
            "cache_control": {"type": "ephemeral"}
         }
      ],
      "messages": messages,
   }
   if config["thinking"]:
      api_kwargs["thinking"] = config["thinking"]

   # Tools
   tools = []
   if registry and config.get("tool_categories"):
      tools = registry.get_tools_for_agent(agent_name)
   if tools:
      api_kwargs["tools"] = tools
      api_kwargs["extra_headers"] = {"anthropic-beta": "token-efficient-tools-2025-02-19"}

   total_input_tokens = 0
   total_output_tokens = 0
   total_cache_creation = 0
   total_cache_read = 0
   response_text = ""
   thinking_text = ""
   iteration = 0
   max_iterations = config.get("max_tool_iterations", 10)

   start_time = time.time()
   response ,model_used = _make_api_call(api_kwargs)

   while True:
      usage = response.usage
      total_input_tokens += usage.input_tokens
      total_output_tokens += usage.output_tokens
      total_cache_creation += getattr(usage, "cache_creation_input_tokens", 0)
      total_cache_read += getattr(usage, "cache_read_input_tokens", 0)

      for block in response.content:
         if block.type == "thinking":
            thinking_text += block.thinking
         elif block.type == "text":
            response_text += block.text

      if response.stop_reason != "tool_use":
         if response.stop_reason == "max_tokens":
            logger.warning(f"Agent '{agent_name}' response truncated (max_tokens hit)")
         break

      iteration += 1
      if iteration >= max_iterations:
         logger.warning(f"Agent '{agent_name}' hit tool iteration limit ({max_iterations})")
         break

      messages.append({"role": "assistant", "content": _serialize_content(response.content)})

      # Tool execution
      tool_results = []
      for block in response.content:
         if block.type == "tool_use":
            result = registry.execute(block.name, block.input)
            tool_results.append({
               "type": "tool_result",
               "tool_use_id": block.id,
               "content": result,
            })

      messages.append({"role": "user", "content": tool_results})

      api_kwargs["messages"] = messages
      response , model_used = _make_api_call(api_kwargs)

   latency = time.time() - start_time

   total_cost, _, _, _, _ = calculate_cost(
       model_used, total_input_tokens, total_output_tokens,
       total_cache_creation, total_cache_read
   )

   log_call(
       agent_name, model_used, user_message, response_text,
       thinking_text, total_input_tokens, total_output_tokens,
       total_cache_creation, total_cache_read,
       total_cost, latency
   )

   if iteration == 0:
      messages.append({"role": "assistant", "content": response_text})
   else:
      messages.append({"role": "assistant", "content": _serialize_content(response.content)})

   return {
      "agent": agent_name,
      "text": response_text,
      "thinking": thinking_text,
      "history": messages,
      "model": model_used,
      "cost": total_cost,
      "latency": latency,
      "tool_iterations": iteration,
      "confidence_summary": scan_confidence_markers(response_text),
   }

async def call_agent_async(agent_name, user_message, history=None, registry=None):
   if history is None:
      history = []
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

   messages = history + [{"role": "user", "content": full_message}]

   api_kwargs = {
      "model": config["model"],
      "max_tokens": config["max_tokens"],
      "system": [
         {
            "type": "text",
            "text": skill,
            "cache_control": {"type": "ephemeral"}
         }
      ],
      "messages": messages,
   }
   if config["thinking"]:
      api_kwargs["thinking"] = config["thinking"]

   tools = []
   if registry and config.get("tool_categories"):
      tools = registry.get_tools_for_agent(agent_name)
   if tools:
      api_kwargs["tools"] = tools
      api_kwargs["extra_headers"] = {"anthropic-beta": "token-efficient-tools-2025-02-19"}

   total_input_tokens = 0
   total_output_tokens = 0
   total_cache_creation = 0
   total_cache_read = 0
   response_text = ""
   thinking_text = ""
   iteration = 0
   max_iterations = config.get("max_tool_iterations", 10)

   start_time = time.time()
   response, model_used = await _make_api_call_async(api_kwargs)

   while True:
      usage = response.usage
      total_input_tokens += usage.input_tokens
      total_output_tokens += usage.output_tokens
      total_cache_creation += getattr(usage, "cache_creation_input_tokens", 0)
      total_cache_read += getattr(usage, "cache_read_input_tokens", 0)

      for block in response.content:
         if block.type == "thinking":
            thinking_text += block.thinking
         elif block.type == "text":
            response_text += block.text

      if response.stop_reason != "tool_use":
         if response.stop_reason == "max_tokens":
            logger.warning(f"Agent '{agent_name}' response truncated (max_tokens hit)")
         break

      iteration += 1
      if iteration >= max_iterations:
         logger.warning(f"Agent '{agent_name}' hit tool iteration limit ({max_iterations})")
         break

      messages.append({"role": "assistant", "content": _serialize_content(response.content)})

      tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
      results = await asyncio.gather(*[
         asyncio.to_thread(registry.execute, b.name, b.input)
         for b in tool_use_blocks
      ])
      tool_results = [
         {"type": "tool_result", "tool_use_id": b.id, "content": r}
         for b, r in zip(tool_use_blocks, results)
      ]

      messages.append({"role": "user", "content": tool_results})

      api_kwargs["messages"] = messages
      response, model_used = await _make_api_call_async(api_kwargs)

   latency = time.time() - start_time

   total_cost, _, _, _, _ = calculate_cost(
       model_used, total_input_tokens, total_output_tokens,
       total_cache_creation, total_cache_read
   )

   log_call(
       agent_name, model_used, user_message, response_text,
       thinking_text, total_input_tokens, total_output_tokens,
       total_cache_creation, total_cache_read,
       total_cost, latency
   )

   if iteration == 0:
      messages.append({"role": "assistant", "content": response_text})
   else:
      messages.append({"role": "assistant", "content": _serialize_content(response.content)})

   return {
      "agent": agent_name,
      "text": response_text,
      "thinking": thinking_text,
      "history": messages,
      "model": model_used,
      "cost": total_cost,
      "latency": latency,
      "tool_iterations": iteration,
      "confidence_summary": scan_confidence_markers(response_text),
   }


