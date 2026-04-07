"""
server.py - FastAPI backend for the Agentic AI frontend.

Endpoints:
    GET  /              -> serves index.html
    POST /chat          -> runs agent, returns full response
    GET  /chat/stream   -> runs agent, streams response as SSE events
    GET  /tasks         -> returns task log as JSON

Run with:
    uvicorn server:app --reload --port 8000
"""

import asyncio
import json
import os
from typing import AsyncGenerator

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tools import registry, _task_log
from agent import Agent
from main import SYSTEM_PROMPT, build_agent
import guardrails
import model_router


app = FastAPI(title="Orion")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Request model ──────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    image_data: str | None = None
    image_type: str | None = None
    active_tools: list[str] | None = None   # None = use all tools


# ── Session store (one Agent per session) ─────────────────────────────────────
# Each session keeps its own conversation history so multi-turn chat works.

_sessions: dict[str, Agent] = {}

def get_agent(session_id: str) -> Agent:
    if session_id not in _sessions:
        _sessions[session_id] = build_agent(verbose=True)
    return _sessions[session_id]


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join("static", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    SSE endpoint (POST) — streams agent output as it happens.

    Accepts JSON body with message, optional image (base64 + media_type).

    Event types sent to the browser:
        data: {"type": "tool",   "name": "get_weather", "input": {...}}
        data: {"type": "text",   "chunk": "..."}
        data: {"type": "done",   "full": "complete final answer"}
        data: {"type": "error",  "message": "..."}
    """
    agent = get_agent(req.session_id)
    queue: asyncio.Queue = asyncio.Queue()

    async def on_text(chunk: str):
        await queue.put({"type": "text", "chunk": chunk})

    async def on_tool(name: str, tool_input: dict):
        await queue.put({"type": "tool", "name": name, "input": tool_input})

    async def on_model(tier: str, model_id: str):
        await queue.put({"type": "model", "tier": tier, "model": model_id})

    async def run_agent():
        try:
            full = await agent.run(
                req.message,
                on_text=on_text,
                on_tool=on_tool,
                on_model=on_model,
                image_data=req.image_data,
                image_type=req.image_type,
                active_tools=req.active_tools,
            )
            await queue.put({"type": "done", "full": full})
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})
        finally:
            await queue.put(None)

    async def event_generator() -> AsyncGenerator[str, None]:
        asyncio.create_task(run_agent())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/session/reset")
async def reset_session(session_id: str = "default"):
    if session_id in _sessions:
        _sessions[session_id].reset()
    return {"status": "reset"}


@app.get("/tasks")
async def list_tasks():
    return JSONResponse(content=list(_task_log.values()))


@app.get("/tools")
async def list_tools():
    return {"tools": registry.list_tools()}


@app.get("/guardrails")
async def list_guardrail_events():
    """Return the last 200 guardrail events (blocked inputs, tool audits, etc.)."""
    return JSONResponse(content=guardrails.get_log())


@app.get("/guardrails/rules")
async def guardrail_rules():
    """Return the current guardrail configuration — tool risk levels and limits."""
    return {
        "input_max_chars": guardrails.INPUT_MAX_CHARS,
        "tool_risk_levels": guardrails.TOOL_RISK,
        "high_risk_tools":  list(guardrails.HIGH_RISK_TOOLS),
    }


@app.get("/router/preview")
async def router_preview(message: str, active_tools: str = ""):
    """Preview which model tier would be chosen for a given message + tool set."""
    tools = [t.strip() for t in active_tools.split(",") if t.strip()] or None
    model_id, tier = model_router.route(message, tools)
    return {"tier": tier, "model": model_id, "label": model_router.TIER_LABELS[tier]}
