"""
tool_registry.py — Build-from-scratch tool system.

How it works:
  1. You decorate a Python function with @registry.tool(...)
  2. The registry stores: name, description, JSON schema, and the function
  3. When Claude calls a tool, the registry looks up the function and runs it
  4. The result is returned to Claude as a tool_result message

This replaces what @beta_tool + create_sdk_mcp_server does in the Agent SDK.
"""

import inspect
import json
from typing import Callable, Any


class ToolRegistry:
    """
    Central store for all tools the agent can call.

    Internally it keeps a dict like:
    {
        "get_weather": {
            "fn": <async function>,
            "schema": {
                "name": "get_weather",
                "description": "...",
                "input_schema": { "type": "object", "properties": {...} }
            }
        },
        ...
    }
    """

    def __init__(self):
        self._tools: dict[str, dict] = {}

    # ── Registration ───────────────────────────────────────────────────────────

    def tool(self, name: str, description: str, properties: dict, required: list = None):
        """
        Decorator that registers a function as a callable tool.

        Args:
            name        : Tool name Claude will use to call it
            description : Natural language description (Claude reads this to decide when to use it)
            properties  : Dict of parameter definitions in JSON Schema format
            required    : List of required parameter names

        Example:
            @registry.tool(
                name="get_weather",
                description="Get current weather for a city",
                properties={"city": {"type": "string", "description": "City name"}},
                required=["city"],
            )
            async def get_weather(args: dict) -> str:
                ...
        """
        def decorator(fn: Callable):
            self._tools[name] = {
                "fn": fn,
                "schema": {
                    "name": name,
                    "description": description,
                    "input_schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required or list(properties.keys()),
                    },
                },
            }
            return fn
        return decorator

    # ── Schema export ──────────────────────────────────────────────────────────

    def get_schemas(self) -> list[dict]:
        """
        Returns the list of tool schemas to pass to the Anthropic API.
        Claude reads these to know what tools exist and how to call them.
        """
        return [entry["schema"] for entry in self._tools.values()]

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    # ── Execution ──────────────────────────────────────────────────────────────

    async def execute(self, name: str, input_data: dict) -> str:
        """
        Execute a tool by name with the given input.

        Claude outputs:
            {"type": "tool_use", "name": "get_weather", "input": {"city": "London"}}

        We look up "get_weather", call the function with {"city": "London"},
        and return the string result back to Claude.
        """
        if name not in self._tools:
            return f"Error: tool '{name}' not registered. Available: {self.list_tools()}"

        fn = self._tools[name]["fn"]
        try:
            # Support both async and sync functions
            if inspect.iscoroutinefunction(fn):
                result = await fn(input_data)
            else:
                result = fn(input_data)

            # Always return a string — Claude expects string content in tool_result
            if isinstance(result, (dict, list)):
                return json.dumps(result, indent=2)
            return str(result)

        except Exception as e:
            return f"Tool '{name}' raised an error: {type(e).__name__}: {e}"
