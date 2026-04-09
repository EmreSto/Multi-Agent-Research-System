import json
import logging
from typing import Any, Callable

from config.agent_config import AGENT_CONFIG

logger = logging.getLogger(__name__)


#Tool registry

class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, dict[str, Any]] = {}
        self._categories: dict[str, list[str]] = {}

    def register(self, name: str, schema: dict, handler: Callable, category: str) -> None:
        self._tools[name] = {
            "schema": schema,
            "handler": handler,
            "category": category,
        }
        self._categories.setdefault(category, []).append(name)
        logger.debug(f"Registered tool '{name}' in category '{category}'")

    def get_tools_for_agent(self, agent_name: str) -> list[dict]:
        categories = AGENT_CONFIG.get(agent_name, {}).get("tool_categories", [])
        tools = []
        for category in categories:
            for tool_name in self._categories.get(category, []):
                tools.append(self._tools[tool_name]["schema"])
        return tools

    def execute(self, tool_name: str, tool_input: dict) -> str:
        if tool_name not in self._tools:
            return json.dumps({
                "error": "unknown_tool",
                "message": f"Tool '{tool_name}' is not registered",
            })

        handler = self._tools[tool_name]["handler"]
        try:
            result = handler(tool_input)
            if isinstance(result, str):
                return result
            return json.dumps(result)
        except Exception as e:
            logger.error(f"Tool '{tool_name}' failed: {e}")
            return json.dumps({
                "error": "execution_error",
                "message": str(e),
            })

    @property
    def registered_tools(self) -> list[str]:
        return list(self._tools.keys())

    @property
    def registered_categories(self) -> list[str]:
        return list(self._categories.keys())
