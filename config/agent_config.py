#Models , update it when a new model is online.
MODELS = {
    "opus":"claude-opus-4-6",
    "sonnet":"claude-sonnet-4-6",
    "haiku" :"claude-haiku-4-5-20251001"
}
#Pricing per million tokens.
PRICING = {
  MODELS["opus"]:{
   "input" :5.0,
   "output":25.0,
   "cache_write": 6.25,
   "1h_cache_write":10.0,
   "cache_read" : 0.50,
  },
  MODELS["sonnet"]:{
   "input" :3.0,
   "output":15.0,
   "cache_write":3.75,
   "1h_cache_write":6.0,
   "cache_read":0.30,
  },
  MODELS["haiku"]:{
   "input":1.0,
   "output":5.0,
   "cache_write":1.25,
   "1h_cache_write":2.0,
   "cache_read":0.10,
  },
}
#PER AGENT CONFIG
#Each agent gets:
# Model -> Which model to use. 
# max_tokens -> Max output tokens.
# thinking -> adaptive thinking (None = disabled).
#Each agent uses models that is optimal for the work that the agent will be doing.

AGENT_CONFIG = {
 "orchestrator":{
   "model":MODELS["sonnet"],
   "max_tokens":2048,
   "thinking": None,
   "tool_categories": [],
   "max_tool_iterations": 3,
 },
 "mathematician":{
  "model":MODELS["opus"],
  "max_tokens":8192,
  "thinking":{"type":"adaptive"},
  "tool_categories": [],
  "max_tool_iterations": 5,
 },
 "statistician":{
  "model":MODELS["opus"],
  "max_tokens":8192,
  "thinking":{"type":"adaptive"},
  "tool_categories": ["code_execution"],
  "max_tool_iterations": 10,
 },
 "ml_engineer":{
  "model":MODELS["sonnet"],
  "max_tokens":4096,
  "thinking":None,
  "tool_categories": ["code_execution", "finance"],
  "max_tool_iterations": 10,
 },
 "domain_expert":{
  "model":MODELS["sonnet"],
  "max_tokens":4096,
  "thinking":{"type":"adaptive"},
  "tool_categories": [],
  "max_tool_iterations": 5,
 },
 "code_optimizer":{
  "model":MODELS["haiku"],
  "max_tokens":2048,
  "thinking": None,
  "tool_categories": ["code_execution"],
  "max_tool_iterations": 10,
 },
 "teacher":{
  "model":MODELS["opus"],
  "max_tokens":8192,
  "thinking":{"type":"adaptive"},
  "tool_categories": ["research", "memory", "visualization", "code_execution"],
  "max_tool_iterations": 15,
 },
}
#RCS CONFIG
RCS_CONFIG = {
    "summary_length": 200,
    "relevance_threshold": 7,
    "top_k": 15,
    "max_workers": 15,
}

#Cost Guardrails 
#Safety limits to prevent runaway spending during development.

MONTHLY_BUDGET_USD = 30.0     
PER_SESSION_WARN_USD = 2.0