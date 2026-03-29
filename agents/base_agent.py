import json
import logging
import re
import time
import datetime
import os
from dotenv import load_dotenv
from pathlib import Path
from anthropic import Anthropic
from config.agent_config import MODELS, PRICING, AGENT_CONFIG

logger = logging.getLogger(__name__)

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




def scan_confidence_markers(text: str) -> dict:
   """Layer 0: Scan agent output for confidence markers.

   Returns a summary of VERIFIED, HIGH_CONFIDENCE, and RECALLED counts.
   Used by Layer 4 (should_stop_pipeline) for halt decisions.
   """
   verified = len(re.findall(r"\[VERIFIED\]", text, re.IGNORECASE))
   high_confidence = len(re.findall(
      r"\[HIGH.CONFIDENCE\]|HIGH.CONFIDENCE(?:\s*[—–-])", text, re.IGNORECASE
   ))
   recalled = len(re.findall(
      r"\[RECALLED\b|RECALLED\s*[—–-]", text, re.IGNORECASE
   ))
   return {
      "verified": verified,
      "high_confidence": high_confidence,
      "recalled": recalled,
      "has_recalled": recalled > 0,
   }


def _serialize_content(content_blocks):
   """Convert response content blocks to serializable dicts for message history."""
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

   # Add tools if agent has tool categories and registry is provided
   tools = []
   if registry and config.get("tool_categories"):
      tools = registry.get_tools_for_agent(agent_name)
   if tools:
      api_kwargs["tools"] = tools
      api_kwargs["extra_headers"] = {"anthropic-beta": "token-efficient-tools-2025-02-19"}

   # Track cumulative metrics across tool loop iterations
   total_input_tokens = 0
   total_output_tokens = 0
   total_cache_creation = 0
   total_cache_read = 0
   response_text = ""
   thinking_text = ""
   iteration = 0
   max_iterations = config.get("max_tool_iterations", 10)

   start_time = time.time()
   response = client.messages.create(**api_kwargs)

   while True:
      # Accumulate token usage from this iteration
      usage = response.usage
      total_input_tokens += usage.input_tokens
      total_output_tokens += usage.output_tokens
      total_cache_creation += getattr(usage, "cache_creation_input_tokens", 0)
      total_cache_read += getattr(usage, "cache_read_input_tokens", 0)

      # Extract text and thinking from this iteration
      for block in response.content:
         if block.type == "thinking":
            thinking_text += block.thinking
         elif block.type == "text":
            response_text += block.text

      # Done if model didn't request tool use
      if response.stop_reason != "tool_use":
         if response.stop_reason == "max_tokens":
            logger.warning(f"Agent '{agent_name}' response truncated (max_tokens hit)")
         break

      # Safety: check iteration limit
      iteration += 1
      if iteration >= max_iterations:
         logger.warning(f"Agent '{agent_name}' hit tool iteration limit ({max_iterations})")
         break

      # Append assistant response (with tool_use blocks) to messages
      messages.append({"role": "assistant", "content": _serialize_content(response.content)})

      # Execute all tool calls in this response (handles parallel tool use)
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

      # Next iteration
      api_kwargs["messages"] = messages
      response = client.messages.create(**api_kwargs)

   latency = time.time() - start_time

   # Calculate cumulative cost across all iterations
   total_cost, _, _, _, _ = calculate_cost(
       config["model"], total_input_tokens, total_output_tokens,
       total_cache_creation, total_cache_read
   )

   log_call(
       agent_name, config["model"], user_message, response_text,
       thinking_text, total_input_tokens, total_output_tokens,
       total_cache_creation, total_cache_read,
       total_cost, latency
   )

   # Append final assistant message to history
   if iteration == 0:
      messages.append({"role": "assistant", "content": response_text})
   else:
      messages.append({"role": "assistant", "content": _serialize_content(response.content)})

   return {
      "agent": agent_name,
      "text": response_text,
      "thinking": thinking_text,
      "history": messages,
      "model": config["model"],
      "cost": total_cost,
      "latency": latency,
      "tool_iterations": iteration,
      "confidence_summary": scan_confidence_markers(response_text),
   }

def read_source(filepath):
   if not os.path.exists(filepath):
      return None

   if filepath.endswith(".pdf"):
      import fitz
      doc = fitz.open(filepath)
      text = ""
      for page in doc:
         text += page.get_text()
      doc.close()
      return text
   elif filepath.endswith((".txt", ".md")):
      with open(filepath, "r") as f:
         return f.read()
   else:
      raise ValueError("Unsupported file type")
      