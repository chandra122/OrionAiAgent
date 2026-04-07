"""
model_router.py — Automatically select the right Claude model for each request.

Instead of always using the most powerful (and expensive) model, Orion now picks
the lightest model that can handle the task well:

  Tier    Model                        Use case
  ──────  ───────────────────────────  ──────────────────────────────────────────
  haiku   claude-haiku-4-5-20251001    Simple 1-tool queries: date, calculator,
                                       weather — fast (< 1s), cheapest
  sonnet  claude-sonnet-4-6            Web search, file reads, moderate reasoning
                                       — balanced speed + quality
  opus    claude-opus-4-6              Code execution, file writes, scheduling,
                                       long/multi-step prompts — full power

Routing is purely rules-based (zero extra API calls, zero added latency):

  Signal                              → Tier
  ──────────────────────────────────  ───────
  Any high-risk tool active           → opus
  Input > 400 chars                   → opus
  Complexity keyword in input         → opus
  Any medium-risk tool active         → sonnet
  Input ≤ 120 chars + only low tools  → haiku
  Anything else                       → sonnet   (safe default)
"""

import re

# ── Model identifiers ──────────────────────────────────────────────────────────

MODELS: dict[str, str] = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-6",
}

# Human-readable cost labels shown in the UI
TIER_LABELS: dict[str, str] = {
    "haiku":  "haiku  · fast",
    "sonnet": "sonnet · balanced",
    "opus":   "opus   · full power",
}

# ── Tool → tier mapping ────────────────────────────────────────────────────────

# High-risk tools require opus (complex reasoning + careful output)
_OPUS_TOOLS: set[str] = {"run_python", "write_file", "schedule_task", "apply_job"}

# Medium-risk tools need at least sonnet
_SONNET_TOOLS: set[str] = {"web_search", "read_file", "cancel_task", "search_jobs"}

# Low-risk tools — haiku can handle these alone
_HAIKU_TOOLS: set[str] = {"get_datetime", "calculate", "get_weather", "list_tasks", "list_applications"}

# ── Complexity signals in user text ───────────────────────────────────────────

# Any of these → opus  (multi-step, code, file, scheduling intent)
_OPUS_PATTERNS: list[str] = [
    r"\bwrite\s+(a\s+)?(file|script|program|code|report|function)\b",
    r"\bcreate\s+(a\s+)?(file|script|program|class|function)\b",
    r"\brun\b.*\bcode\b",
    r"\bexecute\b",
    r"\bschedule\b",
    r"\bautomat(e|ion)\b",
    r"\bstep[- ]by[- ]step\b",
    r"\bmultiple\s+tasks?\b",
    r"\bfirst.*then\b",
    r"\bdeploy\b",
    r"\binstall\b",
    r"\bdebug\b",
    r"\banalyse?\b",
    r"\bbuild\b",
    r"\brefactor\b",
    r"\bgener(ate|ating)\b",
    r"\bapply\s+(to|for)\b",
    r"\bsubmit\s+(my\s+)?application\b",
    r"\bauto.?apply\b",
]

# ── Length thresholds ──────────────────────────────────────────────────────────

_HAIKU_MAX_CHARS = 120   # very short → haiku eligible
_OPUS_MIN_CHARS  = 400   # definitely complex → opus


# ── Public API ─────────────────────────────────────────────────────────────────

def route(
    user_input: str,
    active_tools: list[str] | None = None,
) -> tuple[str, str]:
    """
    Pick the most appropriate Claude model for this request.

    Args:
        user_input   — the user's raw message
        active_tools — list of tool names the agent may use (None = all tools)

    Returns:
        (model_id, tier)   e.g. ("claude-haiku-4-5-20251001", "haiku")
    """
    text  = user_input.strip().lower()
    tools = set(active_tools) if active_tools is not None else set()

    # ── 1. High-risk tools → always opus ──────────────────────────────────────
    if tools & _OPUS_TOOLS:
        return MODELS["opus"], "opus"

    # ── 2. Long input → opus ──────────────────────────────────────────────────
    if len(user_input) > _OPUS_MIN_CHARS:
        return MODELS["opus"], "opus"

    # ── 3. Complexity keywords in the message → opus ──────────────────────────
    for pattern in _OPUS_PATTERNS:
        if re.search(pattern, text):
            return MODELS["opus"], "opus"

    # ── 4. Medium-risk tools → sonnet ─────────────────────────────────────────
    if tools & _SONNET_TOOLS:
        return MODELS["sonnet"], "sonnet"

    # ── 5. Short + only low-risk tools → haiku ────────────────────────────────
    if len(user_input) <= _HAIKU_MAX_CHARS and (not tools or tools <= _HAIKU_TOOLS):
        return MODELS["haiku"], "haiku"

    # ── 6. Default: sonnet (balanced) ─────────────────────────────────────────
    return MODELS["sonnet"], "sonnet"


def thinking_params(tier: str) -> dict:
    """
    Return the `thinking` parameter appropriate for each model tier.

    Haiku doesn't support extended thinking; opus/sonnet use adaptive mode
    (Claude decides when thinking adds value).
    """
    if tier == "haiku":
        return {}   # no thinking parameter — haiku doesn't support it
    return {"thinking": {"type": "adaptive"}}
