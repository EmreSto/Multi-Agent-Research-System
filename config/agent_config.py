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
   "thinking" : None, #Only routes so no deep thinking needed.
 },
 "mathematician":{
  "model":MODELS["opus"],
  "max_tokens":8192,
  "thinking":{"type":"adaptive"},
 },
 "statistician":{
  "model":MODELS["opus"],
  "max_tokens":8192,
  "thinking":{"type":"adaptive"},
 },
 "ml_engineer":{
  "model":MODELS["sonnet"],
  "max_tokens":4096,
  "thinking":None,
 },
 "quant_specialist":{
  "model":MODELS["sonnet"],
  "max_tokens":4096,
  "thinking":{"type":"adaptive"},
 },
 "code_optimizer":{
  "model":MODELS["haiku"],
  "max_tokens":2048,
  "thinking": None,
 },
 "teacher":{
  "model":MODELS["opus"],
  "max_tokens":8192,
  "thinking":{"type":"adaptive"},
 },
}
#Cost Guardrails 
#Safety limits to prevent runaway spending during development.

MONTHLY_BUDGET_USD = 30.0     
PER_SESSION_WARN_USD = 2.0