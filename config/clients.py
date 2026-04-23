"""Singleton Anthropic clients.

One sync + one async client shared across all agents and tools.
Loads .env here so any import path into this module is self-sufficient.
"""

from anthropic import Anthropic, AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()

MAX_RETRIES = 3
TIMEOUT_SECONDS = 120.0

sync_client = Anthropic(max_retries=MAX_RETRIES, timeout=TIMEOUT_SECONDS)
async_client = AsyncAnthropic(max_retries=MAX_RETRIES, timeout=TIMEOUT_SECONDS)
