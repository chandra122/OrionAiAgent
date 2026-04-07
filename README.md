# Orion — Agentic AI from Scratch

> A production-grade AI agent built on the raw Anthropic SDK — no frameworks, no magic.
> Just a manual tool-use loop, intelligent model routing, 3-layer guardrails, and a clean browser UI.

---

## What Makes This Different

Most "AI agent" projects wrap LangChain, use the Agent SDK, or delegate the hard parts to a framework.
Orion doesn't.

Every part of the agentic loop is hand-written:

```
User Input
    │
    ▼
┌─────────────────────────────────────────────────┐
│  Layer 1: Guardrail — Input Check               │  ← jailbreak, PII, length, shell injection
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  Model Router — Zero Latency Decision           │  ← haiku / sonnet / opus in < 1ms
└──────────────────────┬──────────────────────────┘
                       │
         ┌─────────────▼─────────────┐
         │   Claude API (raw SDK)    │
         └─────────────┬─────────────┘
                       │
            stop_reason == tool_use?
                  /           \
                Yes            No
                 │              │
                 ▼              ▼
    ┌────────────────────┐   Stream final
    │ Layer 2: Guardrail │   response to UI
    │ Tool call check    │
    └────────┬───────────┘
             │
             ▼
      Execute tool locally
             │
             ▼
      Append tool result
      to message history
             │
             └──────────────► Loop back to Claude
                               (up to 50 iterations)
                                       │
                               ┌───────▼────────┐
                               │ Layer 3:       │
                               │ Output check   │  ← secrets, leaked keys
                               └───────┬────────┘
                                       │
                                Stream to browser via SSE
```

---

## Features

### Core Agent
- Manual `while True` agentic loop — no SDK, no LangChain
- Up to 50 tool-use iterations per request
- Full conversation history maintained per session
- SSE (Server-Sent Events) streaming — responses appear word by word

### 3-Layer Guardrails
| Layer | When | What it checks |
|---|---|---|
| Input | Before Claude sees anything | Jailbreak patterns, length limits, shell injection, PII |
| Tool call | Before each tool executes | `run_python` sandboxing, `write_file` path protection |
| Output | Before response reaches user | API keys, PEM keys, hardcoded secrets |

### Intelligent Model Routing
Zero-latency rules-based routing — no extra API calls:

| Signal | Routed to |
|---|---|
| High-risk tool active (run_python, apply_job...) | claude-opus-4-6 |
| Long / complex input (400+ chars) | claude-opus-4-6 |
| Medium-risk tools (web_search, read_file...) | claude-sonnet-4-6 |
| Short greeting + low-risk tools | claude-haiku-4-5 |

### Job Auto-Apply (Playwright)
Orion can find and fill out real job applications while you watch:
- Searches Greenhouse and Lever job boards via DuckDuckGo (no API key)
- Opens a live Chromium browser — you see every field being filled
- Handles text fields, dropdowns, radio buttons, file upload (resume), cover letter
- Logs every attempt to `applied_jobs.json` regardless of success

---

## Tools (13 total)

| Tool | Risk | What it does |
|---|---|---|
| `get_datetime` | low | Current date and time |
| `calculate` | low | Safe math expression evaluator |
| `get_weather` | low | Live weather via Open-Meteo (free, no key) |
| `list_tasks` | low | Show all scheduled tasks |
| `list_applications` | low | Job application history |
| `web_search` | low | DuckDuckGo search (free, no key) |
| `read_file` | medium | Read any file from disk |
| `cancel_task` | medium | Cancel a scheduled task |
| `search_jobs` | medium | Find jobs on Greenhouse + Lever |
| `write_file` | high | Write content to disk |
| `run_python` | high | Execute Python code (sandboxed, 15s timeout) |
| `schedule_task` | high | Schedule a future task via APScheduler |
| `apply_job` | high | Auto-apply to a Greenhouse or Lever job |

---

## Stack

| Layer | Technology |
|---|---|
| AI | Anthropic Claude (claude-opus-4-6 / sonnet / haiku) |
| Backend | Python 3.11 + FastAPI + uvicorn |
| Frontend | Plain HTML + CSS + vanilla JS (no React, no npm) |
| Browser automation | Playwright (sync API, runs in thread executor) |
| Job search | DuckDuckGo `ddgs` (no API key needed) |
| Scheduling | APScheduler |
| Streaming | Server-Sent Events (SSE via `fetch` POST) |

---

## Setup

### 1. Clone
```bash
git clone https://github.com/your-username/orion-agent.git
cd orion-agent
```

### 2. Create environment
```bash
conda create -n orion python=3.11 -y
conda activate orion
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure
```bash
# Create your .env file
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" > .env
```

### 4. Set up your job profile (optional)
Copy the template and fill in your details:
```bash
cp job_apply/profile.json.example job_apply/profile.json
# Edit profile.json with your name, email, resume path, etc.
```

### 5. Run
```bash
uvicorn server:app --reload --port 8000
# Open http://localhost:8000
```

Or use the CLI:
```bash
python main.py
```

---

## Usage Examples

**Chat with the agent:**
```
What's the weather like in San Francisco?
Search the web for the latest Claude 4 benchmarks
Run this Python code: [your code]
```

**Job search and apply:**
```
Search for AI engineer jobs remote on Greenhouse and Lever
Apply to this job: https://boards.greenhouse.io/company/jobs/12345
List my applications
```

**Schedule a task:**
```
Remind me to check my job applications in 2 hours
```

---

## Architecture Notes

- **No Agent SDK** — the agentic loop is implemented in ~80 lines in `agent.py`
- **One session per browser tab** — `session_id` in the request body, Agent stored in `_sessions` dict
- **SSE via `fetch()` POST** — not `EventSource`, because EventSource only supports GET and can't send a body
- **`thinking={"type": "adaptive"}`** — passed to opus/sonnet only; haiku rejects it
- **APScheduler with no timezone** — required on Windows (avoids `ZoneInfoNotFoundError`)
- **Playwright in thread executor** — `run_in_executor()` wraps sync Playwright to avoid Windows asyncio subprocess error

---

## Project Structure

```
orion-agent/
├── agent.py           # Core agentic loop — tool_use detection, execution, guardrail integration
├── tool_registry.py   # @registry.tool() decorator — name, schema, function
├── tools.py           # All 13 tool implementations
├── guardrails.py      # 3-layer safety system
├── model_router.py    # Rules-based model selection (haiku / sonnet / opus)
├── server.py          # FastAPI server — SSE endpoint, session store, API routes
├── main.py            # CLI entry point + SYSTEM_PROMPT
├── static/
│   └── index.html     # Single-file frontend (dark theme, voice input, tool dropdown)
├── job_apply/
│   ├── detector.py    # ATS detection (Greenhouse / Lever) + search query builder
│   ├── greenhouse.py  # Playwright form filler for Greenhouse
│   ├── lever.py       # Playwright form filler for Lever
│   └── profile.json   # Your personal info + resume path (gitignored)
├── evals/
│   └── run_evals.py   # Automated tool correctness tests
└── requirements.txt
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `POST` | `/chat/stream` | SSE — streams agent response |
| `POST` | `/session/reset` | Clear conversation history |
| `GET` | `/tools` | List all registered tools |
| `GET` | `/guardrails` | Guardrail event log (last 200) |
| `GET` | `/guardrails/rules` | Tool risk levels + limits |
| `GET` | `/router/preview` | Preview model tier for a message |
| `GET` | `/tasks` | List scheduled tasks |

---

## Roadmap

- [x] Manual agentic loop
- [x] 13 tools with tool registry
- [x] 3-layer guardrails
- [x] Intelligent model routing
- [x] SSE streaming UI
- [x] Greenhouse + Lever auto-apply
- [ ] Priority 3: Automated evals
- [ ] Vision support (image tool calls)
- [ ] Memory / long-term context
- [ ] Multi-agent orchestration

---

## License

MIT
