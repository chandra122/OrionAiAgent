"""
guardrails.py — Safety and quality checks for Orion.

Three layers of protection:

  Layer 1 — Rules-based input checks (synchronous, zero latency):
    • Length limit: reject inputs longer than INPUT_MAX_CHARS
    • Blocklist: reject inputs containing jailbreak / dangerous patterns
    • PII detection: warn when input contains phone numbers or email addresses

  Layer 2 — Tool call safeguards (checked before every tool execution):
    • Each tool has a risk rating: low / medium / high
    • High-risk tools (run_python, write_file, schedule_task) get extra validation
    • run_python: rejects code that tries to import os/sys/subprocess or call shell commands
    • write_file: rejects writes to protected system directories

  Layer 3 — Output validation (checked on the final response):
    • Detects and blocks responses that contain leaked secrets
      (API keys, PEM keys, hardcoded passwords)

Usage in agent.py:
    result = guardrails.check_input(user_input)
    if not result.allowed:
        return f"[Guardrail] {result.reason}"

    # before executing each tool:
    result = guardrails.check_tool_call(tool_name, tool_input)
    if not result.allowed:
        # return block reason as tool result (Claude sees it and stops)
        return f"[Guardrail] Tool blocked: {result.reason}"

    # before returning final answer:
    result = guardrails.validate_output(final_text)
    if not result.allowed:
        return "[Guardrail] Response contained sensitive data and was blocked."
"""

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("orion.guardrails")

# ── Constants ──────────────────────────────────────────────────────────────────

INPUT_MAX_CHARS = 8_000  # characters

# Patterns that suggest prompt injection or jailbreak attempts
_JAILBREAK_PATTERNS = [
    "ignore previous instructions",
    "ignore all instructions",
    "ignore your system prompt",
    "disregard your instructions",
    "forget everything above",
    "you are now",
    "act as dan",
    "do anything now",
    "pretend you have no restrictions",
    "bypass your safety",
    "override your programming",
]

# Patterns that suggest attempts to run dangerous shell commands through text
_DANGEROUS_COMMANDS = [
    r"\brm\s+-rf\b",
    r"\bformat\s+c:",
    r"\bdrop\s+table\b",
    r"\bdelete\s+all\s+files\b",
    r"\bsudo\s+rm\b",
    r"\bchmod\s+777\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]

# PII patterns (warn, not block — agent may legitimately discuss these)
_PII_PATTERNS = {
    "email":   r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}\b",
    "phone":   r"\b(\+?1[-.\s]?)?(\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b",
    "ssn":     r"\b\d{3}-\d{2}-\d{4}\b",
    "cc":      r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b",
}

# ── Tool Risk Registry ─────────────────────────────────────────────────────────

TOOL_RISK: dict[str, str] = {
    "get_datetime":       "low",
    "calculate":          "low",
    "get_weather":        "low",
    "web_search":         "low",
    "list_tasks":         "low",
    "list_applications":  "low",
    "read_file":          "medium",
    "cancel_task":        "medium",
    "search_jobs":        "medium",
    "write_file":         "high",
    "run_python":         "high",
    "schedule_task":      "high",
    "apply_job":          "high",
}

HIGH_RISK_TOOLS: set[str] = {name for name, r in TOOL_RISK.items() if r == "high"}

# Patterns in Python code that indicate unsafe operations
_PYTHON_DANGEROUS_PATTERNS = [
    (r"\bimport\s+os\b",          "imports os module"),
    (r"\bimport\s+sys\b",         "imports sys module"),
    (r"\bimport\s+subprocess\b",  "imports subprocess"),
    (r"\bimport\s+shutil\b",      "imports shutil"),
    (r"\b__import__\s*\(",        "uses __import__"),
    (r"\bos\.system\s*\(",        "calls os.system"),
    (r"\bos\.popen\s*\(",         "calls os.popen"),
    (r"\bsubprocess\.",           "uses subprocess"),
    (r"\beval\s*\(",              "uses eval"),
    (r"\bexec\s*\(",              "uses exec"),
    (r"\bcompile\s*\(",           "uses compile"),
    (r"\b__builtins__\b",         "accesses __builtins__"),
    (r"\bopen\s*\(.+['\"]w",      "opens file for writing"),
]

# Protected system paths that write_file cannot target
_PROTECTED_PATHS = [
    "/etc/",
    "/sys/",
    "/proc/",
    "/boot/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "c:\\windows\\",
    "c:\\system32\\",
    "c:\\program files\\",
]

# Patterns that should never appear in output (secrets / credentials)
_SECRETS_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}",         "API key"),
    (r"sk-ant-[a-zA-Z0-9\-]{20,}",   "Anthropic API key"),
    (r"-----BEGIN [A-Z ]+ KEY-----",   "PEM private key"),
    (r"password\s*=\s*['\"][^'\"]+['\"]", "hardcoded password"),
    (r"api_key\s*=\s*['\"][^'\"]+['\"]",  "hardcoded API key"),
    (r"secret\s*=\s*['\"][^'\"]+['\"]",   "hardcoded secret"),
]


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    allowed: bool
    reason: str = ""
    risk: str = "low"          # low / medium / high
    warnings: list[str] = field(default_factory=list)


# ── Guardrail log (in-memory, last 200 events) ─────────────────────────────────

_guardrail_log: list[dict] = []
_LOG_LIMIT = 200


def _log_event(event_type: str, detail: str, allowed: bool):
    entry = {
        "ts":      datetime.now().isoformat(timespec="seconds"),
        "event":   event_type,
        "detail":  detail,
        "allowed": allowed,
    }
    _guardrail_log.append(entry)
    if len(_guardrail_log) > _LOG_LIMIT:
        _guardrail_log.pop(0)
    level = logging.INFO if allowed else logging.WARNING
    logger.log(level, "[guardrail] %s | allowed=%s | %s", event_type, allowed, detail)


def get_log() -> list[dict]:
    """Return a copy of recent guardrail events (for /guardrails API endpoint)."""
    return list(_guardrail_log)


# ── Layer 1: Input checks ──────────────────────────────────────────────────────

def check_input(text: str) -> GuardrailResult:
    """
    Fast, synchronous rules-based input validation.
    Call this before appending the user message to conversation history.
    """
    if not text or not text.strip():
        _log_event("input_empty", "empty input", False)
        return GuardrailResult(allowed=False, reason="Input cannot be empty.")

    # Length check
    if len(text) > INPUT_MAX_CHARS:
        detail = f"length {len(text)} > limit {INPUT_MAX_CHARS}"
        _log_event("input_too_long", detail, False)
        return GuardrailResult(
            allowed=False,
            reason=f"Input is too long ({len(text):,} characters). Please keep it under {INPUT_MAX_CHARS:,} characters.",
        )

    lower = text.lower()

    # Jailbreak / injection patterns
    for pattern in _JAILBREAK_PATTERNS:
        if pattern in lower:
            detail = f"jailbreak pattern: '{pattern}'"
            _log_event("input_jailbreak", detail, False)
            return GuardrailResult(
                allowed=False,
                reason=f"Input blocked: contains a disallowed instruction override pattern.",
            )

    # Dangerous shell command patterns
    for pattern in _DANGEROUS_COMMANDS:
        if re.search(pattern, text, re.IGNORECASE):
            detail = f"dangerous command pattern: {pattern}"
            _log_event("input_dangerous_cmd", detail, False)
            return GuardrailResult(
                allowed=False,
                reason="Input blocked: contains a potentially dangerous system command.",
            )

    # PII detection — warn but allow
    warnings = []
    for pii_type, pii_pattern in _PII_PATTERNS.items():
        if re.search(pii_pattern, text):
            warnings.append(f"Input may contain {pii_type} — handle with care.")
            _log_event("input_pii_detected", pii_type, True)

    _log_event("input_ok", f"length={len(text)}", True)
    return GuardrailResult(allowed=True, warnings=warnings)


# ── Layer 2: Tool call safeguards ─────────────────────────────────────────────

def check_tool_call(tool_name: str, tool_input: dict) -> GuardrailResult:
    """
    Validate a tool call before it is executed.
    Returns GuardrailResult(allowed=False) to block, or allowed=True with risk rating.

    High-risk tools are always logged for audit regardless of whether they pass.
    """
    risk = TOOL_RISK.get(tool_name, "low")

    # ── run_python: scan code for dangerous patterns ───────────────────────────
    if tool_name == "run_python":
        code = tool_input.get("code", "")
        for pattern, description in _PYTHON_DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                detail = f"run_python blocked — {description}"
                _log_event("tool_blocked", detail, False)
                return GuardrailResult(
                    allowed=False,
                    reason=f"This Python code was blocked because it {description}. "
                           "Only safe, sandboxed code is permitted (no OS access, no file writes, no subprocess).",
                    risk="high",
                )

    # ── write_file: protect system directories ────────────────────────────────
    if tool_name == "write_file":
        path = tool_input.get("path", "").lower().replace("\\", "/")
        for protected in _PROTECTED_PATHS:
            if path.startswith(protected.replace("\\", "/")):
                detail = f"write_file blocked — path targets {protected}"
                _log_event("tool_blocked", detail, False)
                return GuardrailResult(
                    allowed=False,
                    reason=f"Writing to '{tool_input.get('path')}' is not allowed. "
                           "System directories are protected.",
                    risk="high",
                )

    # ── Audit log for all high-risk tool calls ────────────────────────────────
    if risk == "high":
        _log_event("tool_high_risk", f"{tool_name} called", True)

    return GuardrailResult(allowed=True, risk=risk)


# ── Layer 3: Output validation ─────────────────────────────────────────────────

def validate_output(text: str) -> GuardrailResult:
    """
    Scan the final response for leaked secrets or credentials.
    Call this on Claude's final text before returning it to the user.
    """
    if not text:
        return GuardrailResult(allowed=True)

    for pattern, description in _SECRETS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            detail = f"output blocked — contains {description}"
            _log_event("output_blocked", detail, False)
            return GuardrailResult(
                allowed=False,
                reason=f"Response was blocked because it contained a {description}. "
                       "Sensitive credentials should not be included in responses.",
            )

    _log_event("output_ok", f"length={len(text)}", True)
    return GuardrailResult(allowed=True)
