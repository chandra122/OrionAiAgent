"""
agent.py — The core Agent class built from scratch.

This implements the full agentic loop manually using the Anthropic API directly.
No Agent SDK. No magic. Just:

  1. Send messages to Claude
  2. Claude responds with text OR tool_use blocks
  3. If tool_use → execute the tool → send result back → repeat
  4. If end_turn → we're done

The conversation history (messages list) is the agent's "memory" within a session.
"""

import json
import anthropic
from typing import Optional
from tool_registry import ToolRegistry
import guardrails
import model_router


# WMO weather code → human readable (used in pretty-printing)
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 51: "Light drizzle", 61: "Light rain", 71: "Light snow",
    80: "Rain showers", 95: "Thunderstorm",
}


class Agent:
    """
    A self-contained AI agent that can reason and call tools in a loop.

    Internal state:
        messages  — full conversation history sent to Claude on every turn
                    (Claude is stateless; we maintain history ourselves)
        iteration — how many turns have happened in the current run
    """

    def __init__(
        self,
        registry: ToolRegistry,
        model: str = "claude-opus-4-6",
        system_prompt: str = "",
        max_iterations: int = 50,
        verbose: bool = True,
    ):
        self.client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env
        self.registry = registry
        self.model = model
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.messages: list[dict] = []        # conversation history

    # ── Public API ─────────────────────────────────────────────────────────────

    def reset(self):
        """Clear conversation history to start fresh."""
        self.messages = []

    async def run(
        self,
        user_input: str,
        on_text=None,
        on_tool=None,
        on_model=None,
        image_data: str | None = None,
        image_type: str | None = None,
        active_tools: list[str] | None = None,
    ) -> str:
        """
        Run the agent on a user prompt.
        Returns the final text response from Claude.

        Optional callbacks for streaming to a frontend:
            on_text(chunk: str)        — called whenever Claude outputs text
            on_tool(name: str, input)  — called when a tool is being executed

        Optional image input:
            image_data  — base64 encoded image string
            image_type  — MIME type e.g. "image/jpeg", "image/png"
        """
        # Build user message content — text only, or text + image
        if image_data and image_type:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_type,
                        "data": image_data,
                    },
                },
                {"type": "text", "text": user_input or "What is in this image?"},
            ]
        else:
            content = user_input

        # ── Guardrail Layer 1: validate user input before it hits Claude ──────────
        input_check = guardrails.check_input(user_input or "")
        if not input_check.allowed:
            rejection = f"[Guardrail] {input_check.reason}"
            if on_text:
                await on_text(rejection)
            return rejection

        if input_check.warnings:
            for w in input_check.warnings:
                self._log(f"  [Guardrail warning] {w}")

        # ── Model routing: pick the lightest model that fits the task ──────────
        chosen_model, tier = model_router.route(user_input or "", active_tools)
        self._log(f"  [Router] tier={tier}  model={chosen_model}")
        if on_model:
            await on_model(tier, chosen_model)

        self.messages.append({"role": "user", "content": content})

        for iteration in range(self.max_iterations):
            self._log(f"\n{'━'*60}")
            self._log(f"  Turn {iteration + 1}  |  model={tier}  |  history={len(self.messages)} msgs")
            self._log(f"{'━'*60}")

            # ── Step 1: Call Claude ────────────────────────────────────────────
            # Filter schemas to only the tools the user has enabled
            schemas = self.registry.get_schemas()
            if active_tools is not None:
                schemas = [s for s in schemas if s["name"] in active_tools]

            # Build API call kwargs — thinking only supported on sonnet/opus
            call_kwargs = dict(
                model=chosen_model,
                max_tokens=16000,
                system=self.system_prompt,
                tools=schemas,
                messages=self.messages,
                **model_router.thinking_params(tier),
            )

            response = self.client.messages.create(**call_kwargs)

            self._log(f"  stop_reason: {response.stop_reason}")
            self._log(f"  tokens used: input={response.usage.input_tokens}, output={response.usage.output_tokens}")

            # ── Step 2: Append Claude's response to history ────────────────────
            # IMPORTANT: we append the raw content list (not just text),
            # because tool_use blocks must be in the history for the API to accept tool_results
            self.messages.append({
                "role": "assistant",
                "content": response.content,   # list of TextBlock / ToolUseBlock / ThinkingBlock
            })

            # ── Step 3: Handle stop reason ─────────────────────────────────────

            if response.stop_reason == "end_turn":
                final = self._extract_text(response)

                # ── Guardrail Layer 3: validate output before returning ────────
                output_check = guardrails.validate_output(final)
                if not output_check.allowed:
                    blocked_msg = f"[Guardrail] {output_check.reason}"
                    if on_text:
                        await on_text(blocked_msg)
                    return blocked_msg

                if on_text and final:
                    await on_text(final)
                self._log("\n[Agent finished]\n")
                return final

            elif response.stop_reason == "tool_use":
                # Claude wants to call one or more tools
                tool_results = await self._execute_tool_calls(response, on_tool)

                # Add all tool results as a single user message
                # The API requires role="user" for tool_result blocks
                self.messages.append({
                    "role": "user",
                    "content": tool_results,
                })

            elif response.stop_reason == "max_tokens":
                self._log("[Warning] Hit max_tokens — response may be truncated")
                return self._extract_text(response)

            else:
                self._log(f"[Warning] Unexpected stop_reason: {response.stop_reason}")
                break

        return "Reached maximum iterations without a final answer."

    # ── Tool Execution ─────────────────────────────────────────────────────────

    async def _execute_tool_calls(self, response, on_tool=None) -> list[dict]:
        """
        Find all tool_use blocks in the response, execute them, and
        return a list of tool_result blocks ready to send back to Claude.

        Claude can request multiple tools in one response — we execute all of them.
        """
        tool_results = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name  = block.name    # e.g. "get_weather"
            tool_input = block.input   # e.g. {"city": "London"}
            tool_id    = block.id      # unique ID we must echo back

            self._log(f"\n  → Calling tool: {tool_name}")
            self._log(f"    Input: {json.dumps(tool_input, indent=4)}")

            # ── Guardrail Layer 2: validate tool call before execution ─────────
            tool_check = guardrails.check_tool_call(tool_name, tool_input)
            if not tool_check.allowed:
                self._log(f"    [Guardrail BLOCKED] {tool_check.reason}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f"[Guardrail] Tool call blocked: {tool_check.reason}",
                    "is_error": True,
                })
                continue

            if tool_check.risk == "high":
                self._log(f"    [Guardrail] High-risk tool '{tool_name}' — logged for audit.")

            if on_tool:
                await on_tool(tool_name, tool_input)

            # Execute the tool via the registry
            result = await self.registry.execute(tool_name, tool_input)

            self._log(f"    Result: {result[:300]}{'...' if len(result) > 300 else ''}")

            # Build the tool_result block
            # tool_use_id MUST match the id from the tool_use block above
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": result,
            })

        return tool_results

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _extract_text(self, response) -> str:
        """Pull all text from the response content blocks."""
        parts = []
        for block in response.content:
            if hasattr(block, "text") and block.type == "text":
                parts.append(block.text)
        return "\n".join(parts)

    def _log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    @property
    def history(self) -> list[dict]:
        """Return a copy of the conversation history."""
        return list(self.messages)

    @property
    def turn_count(self) -> int:
        return len([m for m in self.messages if m["role"] == "user"])
