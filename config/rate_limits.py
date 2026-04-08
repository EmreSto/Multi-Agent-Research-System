from dataclasses import dataclass, field
from datetime import datetime, timezone                                    
import time 
from config.agent_config import FALLBACK_CHAIN

@dataclass
class RateLimitState:
    tokens_remaining: int | None = None
    tokens_limit: int | None = None
    tokens_reset: str | None = None
    requests_remaining: int | None = None
    requests_limit: int | None = None
    requests_reset: str | None = None
    last_updated: float = field(default_factory=time.time) 

    def update_from_headers(self, headers):
       raw_tokens_remaining = headers.get("anthropic-ratelimit-tokens-remaining")
       if raw_tokens_remaining is not None:
           self.tokens_remaining = int(raw_tokens_remaining)
       raw_tokens_limit = headers.get("anthropic-ratelimit-tokens-limit")
       if raw_tokens_limit is not None:
          self.tokens_limit = int(raw_tokens_limit) 
       self.tokens_reset = headers.get("anthropic-ratelimit-tokens-reset", self.tokens_reset)
       raw_requests_remaining = headers.get("anthropic-ratelimit-requests-remaining")
       if raw_requests_remaining is not None:
           self.requests_remaining = int(raw_requests_remaining)
       raw_requests_limit = headers.get("anthropic-ratelimit-requests-limit")
       if raw_requests_limit is not None:
           self.requests_limit = int(raw_requests_limit)
       self.requests_reset = headers.get("anthropic-ratelimit-requests-reset", self.requests_reset)
       self.last_updated = time.time()
    def should_pause(self, token_threshold = 0.1) -> bool:
        if self.tokens_remaining is not None and self.tokens_limit is not None:
            if self.tokens_remaining / self.tokens_limit < token_threshold:
                return True
        return False

    def pause_duration(self) -> float:
        if self.tokens_reset is not None:
            reset_time = datetime.fromisoformat(self.tokens_reset)
            now = datetime.now(timezone.utc)
            timedelta = reset_time - now
            return min(60, max(0, timedelta.total_seconds()))
        else:
            return 0.0
 
            
_rate_limit_states: dict[str, RateLimitState] = {}

def get_rate_limit_state(model: str) -> RateLimitState:
    if model not in _rate_limit_states:
        _rate_limit_states[model] = RateLimitState()
    return _rate_limit_states[model]

def get_fallback_model(model: str) -> str | None:
    return FALLBACK_CHAIN.get(model)
    
    

