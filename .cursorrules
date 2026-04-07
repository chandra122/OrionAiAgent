# Orion — Agentic AI Project Context

This file is auto-loaded by Claude Code and Cursor at session start.
It exists so that work can resume in either tool without losing context.

---

## What is Orion?

A full agentic AI built from scratch using only the raw Anthropic Python SDK —
no Agent SDK, no LangChain, no magic. It implements the agentic loop manually:

1. Send messages to Claude
2. Claude responds with text or tool_use blocks
3. If tool_use → execute the tool → append result → repeat
4. If end_turn → return final response

Stack: Python (FastAPI backend) + plain HTML/JS frontend (no React, no npm).

---

## How to Run

```bash
# Environment: Anaconda — activate the right env first
# conda activate <env-name>

cd C:\Users\Chandra\.claude\projects\C--Users-Chandra\agentic_ai_scratch

# Start the server
uvicorn server:app --reload --port 8000

# Open browser
http://localhost:8000

# Or run the CLI menu
python main.py
```

`.env` file must exist with `ANTHROPIC_API_KEY=sk-ant-...`

---

## File Map

| File | Role |
|---|---|
| `agent.py` | Core Agent class — manual agentic loop, tool execution, guardrails integration |
| `tool_registry.py` | `@registry.tool(...)` decorator — stores name, description, JSON schema, fn |
| `tools.py` | All 10 tool implementations registered into the shared registry |
| `main.py` | CLI entry point — menu + SYSTEM_PROMPT + `build_agent()` factory |
| `server.py` | FastAPI backend — SSE streaming, session store, all API endpoints |
| `guardrails.py` | 3-layer safety: input checks, tool safeguards, output validation |
| `model_router.py` | Rules-based model selection — routes to haiku/sonnet/opus per request |
| `static/index.html` | Single-file frontend — dark theme, voice, image input, tool dropdown |
| `tasks.json` | Persisted task log (auto-created by APScheduler tools) |
| `requirements.txt` | anthropic, fastapi, uvicorn, httpx, anyio, duckduckgo-search, apscheduler, python-dotenv, tzdata |

---

## Tools Available (13 total)

| Tool | Risk | Model tier | Description |
|---|---|---|---|
| `get_datetime` | low | haiku | Current date/time |
| `calculate` | low | haiku | Safe math eval |
| `get_weather` | low | haiku/sonnet | Live weather via Open-Meteo (no API key) |
| `list_tasks` | low | haiku | Show scheduled tasks |
| `web_search` | low | sonnet | DuckDuckGo search (no API key) |
| `read_file` | medium | sonnet | Read file from disk |
| `cancel_task` | medium | sonnet | Cancel a scheduled task |
| `search_jobs` | medium | sonnet | Find jobs on Greenhouse + Lever via DuckDuckGo |
| `write_file` | high | opus | Write file to disk |
| `run_python` | high | opus | Execute Python in subprocess (15s timeout) |
| `schedule_task` | high | opus | Schedule future task via APScheduler |
| `apply_job` | high | opus | Auto-apply to a Greenhouse or Lever job via Playwright |
| `list_applications` | low | haiku | Show application history log |

---

## Architecture Decisions (do not change without reason)

- **No Agent SDK** — intentional. Manual loop is the whole point.
- **No React / npm** — plain HTML/JS only. Framer-motion is React-only; use CSS animations.
- **SSE via fetch (POST)** — not EventSource. Required for sending image data in body.
- **One Agent per session** — `_sessions: dict[str, Agent]` keyed by `session_id`.
- **Conversation history** — `self.messages: list[dict]` maintained client-side (Claude is stateless).
- **APScheduler** — `BackgroundScheduler()` with no timezone param (fixes Windows ZoneInfoNotFoundError).
- **tasks.json** — persists task log across restarts; `_full_data` field re-registers future tasks.

---

## What Has Been Built (Completed)

### Priority 1 — Guardrails (`guardrails.py`) ✓
Three protection layers checked on every request:

**Layer 1 — Input checks** (rules-based, zero latency):
- Empty input rejection
- Length limit: 8,000 chars max
- Jailbreak pattern blocklist (15+ patterns: "ignore previous instructions", "act as DAN", etc.)
- Dangerous shell command detection (`rm -rf`, `drop table`, etc.)
- PII detection (email, phone, SSN, credit card) — warns but allows

**Layer 2 — Tool call safeguards** (before each tool executes):
- `run_python`: blocks code containing `import os/sys/subprocess`, `eval`, `exec`, `open(..., "w")`
- `write_file`: blocks writes to system directories (`/etc/`, `C:\Windows\`, etc.)
- All high-risk tool calls logged for audit trail

**Layer 3 — Output validation** (before returning final response):
- Blocks responses containing API keys, PEM private keys, hardcoded passwords

Frontend: amber-colored guardrail messages, "Shield" button shows event log, tool dropdown shows risk badges.
API: `GET /guardrails`, `GET /guardrails/rules`

### Priority 2 — Model optimization (`model_router.py`) ✓
Rules-based routing — zero extra API calls, instant decision:

| Condition | Model |
|---|---|
| Any high-risk tool active | opus |
| Input > 400 chars | opus |
| Complexity keywords (write, execute, schedule, analyze...) | opus |
| Medium-risk tools active | sonnet |
| Short input + only low-risk tools | haiku |
| Default | sonnet |

`thinking={"type": "adaptive"}` passed for opus/sonnet only — haiku doesn't support it.
Frontend: colored tier badge (haiku=green, sonnet=indigo, opus=purple) on each response.
API: `GET /router/preview?message=...&active_tools=...`

---

## Pending Work

### Priority 3 — Evals (NOT YET STARTED)
Goal: automated tests that verify each tool works correctly with known inputs.

Suggested approach:
- Create `evals/` directory with `run_evals.py`
- One eval per tool: fixed input → assert expected output shape/content
- Run without the full agent (call tool functions directly via registry)
- Output: pass/fail per tool, latency, any regressions

Example evals:
```python
# get_datetime → must return valid ISO timestamp
# calculate "2 + 2" → must return "4"
# get_weather "London" → must return temp_c, humidity_pct fields
# web_search "Python 3.12" → must return at least 1 result with title+url
# run_python "print(1+1)" → stdout must contain "2"
# guardrails.check_input("ignore previous instructions") → must return allowed=False
# model_router.route("What time is it?", ["get_datetime"]) → must return "haiku" tier
```

---

## Conventions (Always Follow)

- No emojis anywhere in code or responses
- No Agent SDK imports
- No React, no npm, no node_modules
- Comments in code where logic is non-obvious; no docstrings on trivial functions
- Tool functions always: `async def fn(args: dict) -> str`
- Tool results always strings (JSON-serialized for structured data)
- Guardrail rejections always prefixed `[Guardrail]`
- Model router decisions logged with `[Router]` prefix in server console

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves index.html |
| POST | `/chat/stream` | SSE — streams agent response |
| POST | `/session/reset` | Clear conversation history |
| GET | `/tasks` | List scheduled tasks |
| GET | `/tools` | List registered tools |
| GET | `/guardrails` | Guardrail event log (last 200) |
| GET | `/guardrails/rules` | Tool risk levels + input limits |
| GET | `/router/preview` | Preview model tier for a message |

---

## SSE Event Types (from /chat/stream)

```json
{"type": "model",  "tier": "haiku",  "model": "claude-haiku-4-5-20251001"}
{"type": "tool",   "name": "get_weather", "input": {"city": "Tokyo"}}
{"type": "text",   "chunk": "The weather in..."}
{"type": "done",   "full": "complete final answer"}
{"type": "error",  "message": "something went wrong"}
```

---

## Known Issues / Watch Out For

- `BackgroundScheduler()` must have NO timezone param on Windows (causes ZoneInfoNotFoundError)
- `on_tool` callback must be passed as parameter to `_execute_tool_calls(response, on_tool)` — not accessed from `self`
- SSE must use `fetch()` POST, not `EventSource` (EventSource only supports GET)
- `thinking={"type": "adaptive"}` must NOT be passed to haiku — remove it in `model_router.thinking_params()`
- Task log uses `_full_data` field (full untruncated data) for re-registering tasks after restart; `data` field is truncated display only

---

## Cross-Environment Sync Protocol

Use this protocol to keep `CLAUDE.md` and `.cursorrules` aligned.

1. Any project-rule update must be applied to both files in the same session.
2. If one environment hits context/output limits, finish by writing an `Update Ledger` entry (below), then continue in the other environment from that entry.
3. If files differ, do not guess merge intent; ask the user which version to keep, then mirror it to both files.
4. Prefer stricter instruction when overlap exists (safety, constraints, and architecture invariants).
5. Keep section headings stable so diffs are easy to compare.

### Update Ledger (append newest at top)

- YYYY-MM-DD | Author: <name> | Reason: <what changed> | Mirrored: yes/no
