"""
tools.py — All tool implementations registered into a shared ToolRegistry.

Each tool:
  - Takes a dict of arguments (as Claude sends them)
  - Returns a string (Claude reads this as the tool result)
  - Is registered via @registry.tool(...)

Tools available:
  get_datetime   — current date/time
  calculate      — safe math eval
  get_weather    — live weather via Open-Meteo (free, no API key)
  web_search     — DuckDuckGo search (free, no API key)
  read_file      — read a file from disk
  write_file     — write a file to disk
  run_python     — execute Python code in a subprocess
  schedule_task  — schedule a task to run at a future time
  list_tasks     — list all scheduled tasks and their status
  cancel_task    — cancel a scheduled task by ID

  Job auto-apply tools (Greenhouse + Lever):
  search_jobs    — find job listings on Greenhouse and Lever via web search
  apply_job      — auto-apply to a Greenhouse or Lever job URL
  list_applications — show application history log
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import httpx

from tool_registry import ToolRegistry

# Shared registry — import this in agent.py
registry = ToolRegistry()


# ── Tool 1: Current Date & Time ────────────────────────────────────────────────

@registry.tool(
    name="get_datetime",
    description="Get the current local date and time.",
    properties={},
    required=[],
)
async def get_datetime(args: dict) -> str:
    now = datetime.datetime.now()
    return json.dumps({
        "date":    now.strftime("%Y-%m-%d"),
        "time":    now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "iso":     now.isoformat(),
    })


# ── Tool 2: Safe Calculator ────────────────────────────────────────────────────

@registry.tool(
    name="calculate",
    description=(
        "Evaluate a mathematical expression and return the result. "
        "Supports: +, -, *, /, //, **, %, (), decimal numbers. "
        "Example: '(100 + 200) * 3.14'"
    ),
    properties={
        "expression": {
            "type": "string",
            "description": "Math expression to evaluate, e.g. '1234 * 5678 + 99**2'",
        }
    },
)
async def calculate(args: dict) -> str:
    expression = args["expression"]

    # Security: only allow safe characters
    allowed = set("0123456789+-*/.()%, **e ")
    if not all(c in allowed for c in expression):
        return "Error: expression contains unsafe characters"

    try:
        # Empty builtins prevents access to Python internals
        result = eval(expression, {"__builtins__": {}})  # noqa: S307
        return str(result)
    except Exception as e:
        return f"Error: {e}"


# ── Tool 3: Live Weather (Open-Meteo, no API key) ──────────────────────────────

@registry.tool(
    name="get_weather",
    description=(
        "Get current weather for any city. "
        "Automatically geocodes the city name to coordinates, then fetches live weather. "
        "Returns temperature (°C), humidity (%), wind speed (mph), and weather code."
    ),
    properties={
        "city": {
            "type": "string",
            "description": "City name, e.g. 'London', 'Tokyo', 'New York'",
        }
    },
)
async def get_weather(args: dict) -> str:
    city = args["city"]

    async with httpx.AsyncClient(timeout=10) as client:
        # Step 1: Geocode city name → lat/lon
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
        )
        geo_data = geo.json()
        if not geo_data.get("results"):
            return f"Error: city '{city}' not found"

        r = geo_data["results"][0]
        lat, lon = r["latitude"], r["longitude"]
        resolved_city = r.get("name", city)
        country = r.get("country", "")

        # Step 2: Fetch weather
        weather = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                "temperature_unit": "celsius",
                "wind_speed_unit": "mph",
            },
        )
        data = weather.json().get("current", {})

    return json.dumps({
        "city":             f"{resolved_city}, {country}",
        "temperature_c":    data.get("temperature_2m"),
        "humidity_pct":     data.get("relative_humidity_2m"),
        "wind_speed_mph":   data.get("wind_speed_10m"),
        "weather_code":     data.get("weather_code"),
    })


# ── Tool 4: Web Search (DuckDuckGo, no API key) ────────────────────────────────

@registry.tool(
    name="web_search",
    description=(
        "Search the web using DuckDuckGo and return the top results. "
        "Each result includes title, URL, and snippet. "
        "Use this for real-time information, news, or anything not in your training data."
    ),
    properties={
        "query": {
            "type": "string",
            "description": "Search query, e.g. 'latest Claude AI news 2025'",
        },
        "max_results": {
            "type": "integer",
            "description": "Number of results to return (default: 5, max: 10)",
        },
    },
    required=["query"],
)
async def web_search(args: dict) -> str:
    query = args["query"]
    max_results = min(int(args.get("max_results", 5)), 10)

    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        if not results:
            return "No results found."
        return json.dumps(results, indent=2)
    except ImportError:
        return "Error: duckduckgo-search not installed. Run: pip install duckduckgo-search"
    except Exception as e:
        return f"Search error: {e}"


# ── Tool 5: Read File ──────────────────────────────────────────────────────────

@registry.tool(
    name="read_file",
    description="Read the contents of a file from disk and return it as text.",
    properties={
        "path": {
            "type": "string",
            "description": "Absolute or relative file path to read",
        }
    },
)
async def read_file(args: dict) -> str:
    path = args["path"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content if content else "(file is empty)"
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


# ── Tool 6: Write File ─────────────────────────────────────────────────────────

@registry.tool(
    name="write_file",
    description="Write text content to a file on disk. Creates the file if it doesn't exist, overwrites if it does.",
    properties={
        "path": {
            "type": "string",
            "description": "File path to write to, e.g. 'report.md' or '/tmp/output.txt'",
        },
        "content": {
            "type": "string",
            "description": "Text content to write into the file",
        },
    },
)
async def write_file(args: dict) -> str:
    path = args["path"]
    content = args["content"]
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        size = os.path.getsize(path)
        return f"File written successfully: {path} ({size} bytes)"
    except Exception as e:
        return f"Error writing file: {e}"


# ── Tool 7: Run Python Code ────────────────────────────────────────────────────

@registry.tool(
    name="run_python",
    description=(
        "Execute a Python code snippet in a sandboxed subprocess and return stdout/stderr. "
        "Use this for calculations, data processing, or any task that needs actual code execution. "
        "The code runs in a temporary file and the result is captured."
    ),
    properties={
        "code": {
            "type": "string",
            "description": "Python code to execute. Use print() to output results.",
        }
    },
)
async def run_python(args: dict) -> str:
    code = args["code"]

    # Write to a temp file and run it — never exec() untrusted code in-process
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=15,  # max 15 seconds
        )
        output = ""
        if result.stdout:
            output += f"stdout:\n{result.stdout}"
        if result.stderr:
            output += f"\nstderr:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: code execution timed out (15s limit)"
    except Exception as e:
        return f"Error running code: {e}"
    finally:
        os.unlink(tmp_path)


# ── Scheduling Tools ───────────────────────────────────────────────────────────
#
# How it works:
#   - A BackgroundScheduler runs in a daemon thread alongside your agent
#   - schedule_task registers a job with APScheduler at a future datetime
#   - The job runs your chosen action (run_python / write_file / print_message)
#   - All tasks are tracked in _task_log so you can list or cancel them
#   - The scheduler shuts down cleanly when the process exits (atexit)

import uuid
import atexit
from apscheduler.schedulers.background import BackgroundScheduler

# File used to persist task log across restarts
_TASKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.json")


def _load_task_log() -> dict:
    """Load task log from disk if it exists."""
    if os.path.exists(_TASKS_FILE):
        try:
            with open(_TASKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_task_log():
    """Persist current task log to disk."""
    try:
        with open(_TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(_task_log, f, indent=2)
    except Exception:
        pass


# Single shared scheduler instance — started once when this module is imported
_scheduler = BackgroundScheduler()
_scheduler.start()
atexit.register(lambda: _scheduler.shutdown(wait=False))

# Task log — loaded from disk, updated on every change
_task_log: dict[str, dict] = _load_task_log()

# On restart: re-register future tasks, expire past ones
for _t in _task_log.values():
    if _t["status"] == "scheduled":
        run_dt = datetime.datetime.strptime(_t["run_at"], "%Y-%m-%d %H:%M:%S")
        if run_dt > datetime.datetime.now():
            # Still in the future — re-register it with the scheduler
            _scheduler.add_job(
                _task_runner,
                trigger="date",
                run_date=run_dt,
                args=[_t["task_id"], _t["action"], _t["_full_data"]],
                id=_t["task_id"],
                replace_existing=True,
            )
        else:
            # Time has already passed — mark expired
            _t["status"] = "expired"
_save_task_log()


def _parse_run_at(run_at_str: str) -> datetime.datetime:
    """
    Parse a time string into a datetime.

    Supported formats:
        "in 5 minutes"   "in 2 hours"   "in 1 day"   "in 30 seconds"
        "15:30"          "15:30:00"
        "2025-04-02 15:30:00"
    """
    s = run_at_str.strip().lower()
    now = datetime.datetime.now()

    if s.startswith("in "):
        parts = s[3:].split()
        if len(parts) >= 2:
            amount = int(parts[0])
            unit = parts[1].rstrip("s")  # remove plural suffix
            mapping = {
                "second": datetime.timedelta(seconds=amount),
                "sec":    datetime.timedelta(seconds=amount),
                "minute": datetime.timedelta(minutes=amount),
                "min":    datetime.timedelta(minutes=amount),
                "hour":   datetime.timedelta(hours=amount),
                "day":    datetime.timedelta(days=amount),
            }
            for key, delta in mapping.items():
                if unit.startswith(key):
                    return now + delta
        raise ValueError(f"Cannot parse relative time: {run_at_str}")

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            dt = datetime.datetime.strptime(run_at_str.strip(), fmt)
            if fmt in ("%H:%M:%S", "%H:%M"):
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
                if dt < now:
                    dt += datetime.timedelta(days=1)  # next occurrence
            return dt
        except ValueError:
            continue

    raise ValueError(
        f"Cannot parse time '{run_at_str}'. "
        "Use 'in 5 minutes', 'in 2 hours', '15:30', or '2025-04-02 15:30:00'."
    )


def _task_runner(task_id: str, action: str, data: str):
    """
    Called by APScheduler at the scheduled time.
    Executes the task and records the result in _task_log.
    """
    log = _task_log.get(task_id, {})
    log["status"] = "running"

    try:
        if action == "print_message":
            output = f"[Scheduled message] {data}"
            print(f"\n{output}\n")

        elif action == "run_python":
            with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
                f.write(data)
                tmp = f.name
            try:
                result = subprocess.run(
                    [sys.executable, tmp],
                    capture_output=True, text=True, timeout=30,
                )
                output = result.stdout or result.stderr or "(no output)"
                print(f"\n[Scheduled task {task_id}] Output:\n{output}\n")
            finally:
                os.unlink(tmp)

        elif action == "write_file":
            # data format: "path::content"
            path, _, content = data.partition("::")
            with open(path.strip(), "w", encoding="utf-8") as f:
                f.write(content)
            output = f"Wrote file: {path.strip()}"
            print(f"\n[Scheduled task {task_id}] {output}\n")

        else:
            output = f"Unknown action: {action}"

        log["status"] = "completed"
        log["output"] = output

    except Exception as e:
        log["status"] = "failed"
        log["output"] = str(e)
        print(f"\n[Scheduled task {task_id}] Failed: {e}\n")

    _task_log[task_id] = log
    _save_task_log()


@registry.tool(
    name="schedule_task",
    description=(
        "Schedule a task to run automatically at a future time. "
        "Actions: 'print_message' (display a message), "
        "'run_python' (execute Python code), "
        "'write_file' (write to a file — use 'path::content' format in data). "
        "Time formats: 'in 5 minutes', 'in 2 hours', 'in 1 day', '15:30', '2025-04-02 15:30:00'."
    ),
    properties={
        "task_id": {
            "type": "string",
            "description": "Optional unique name for the task. Auto-generated if not provided.",
        },
        "action": {
            "type": "string",
            "enum": ["print_message", "run_python", "write_file"],
            "description": "What to do when the task runs.",
        },
        "data": {
            "type": "string",
            "description": (
                "Content for the action. "
                "For print_message: the message text. "
                "For run_python: the Python code. "
                "For write_file: 'filepath::content'."
            ),
        },
        "run_at": {
            "type": "string",
            "description": "When to run. E.g. 'in 5 minutes', 'in 2 hours', '15:30', '2025-04-02 15:30'.",
        },
    },
    required=["action", "data", "run_at"],
)
async def schedule_task(args: dict) -> str:
    task_id  = args.get("task_id") or f"task_{uuid.uuid4().hex[:8]}"
    action   = args["action"]
    data     = args["data"]
    run_at   = args["run_at"]

    try:
        run_dt = _parse_run_at(run_at)
    except ValueError as e:
        return f"Error: {e}"

    if run_dt <= datetime.datetime.now():
        return "Error: scheduled time is in the past. Please provide a future time."

    _task_log[task_id] = {
        "task_id":    task_id,
        "action":     action,
        "data":       data[:100] + ("..." if len(data) > 100 else ""),
        "_full_data": data,          # full copy for re-registration after restart
        "run_at":     run_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "status":     "scheduled",
        "output":     None,
    }

    _scheduler.add_job(
        _task_runner,
        trigger="date",
        run_date=run_dt,
        args=[task_id, action, data],
        id=task_id,
        replace_existing=True,
    )
    _save_task_log()

    return json.dumps({
        "task_id":  task_id,
        "action":   action,
        "run_at":   run_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "status":   "scheduled",
        "message":  f"Task '{task_id}' scheduled to run at {run_dt.strftime('%H:%M:%S on %Y-%m-%d')}.",
    })


@registry.tool(
    name="list_tasks",
    description="List all scheduled tasks — shows task ID, action, scheduled time, and status (scheduled / running / completed / failed / cancelled).",
    properties={},
    required=[],
)
async def list_tasks(args: dict) -> str:
    if not _task_log:
        return "No tasks scheduled."
    return json.dumps(list(_task_log.values()), indent=2)


@registry.tool(
    name="cancel_task",
    description="Cancel a scheduled task by its task ID. Only works if the task has not yet run.",
    properties={
        "task_id": {
            "type": "string",
            "description": "The task ID to cancel (from schedule_task or list_tasks).",
        }
    },
)
async def cancel_task(args: dict) -> str:
    task_id = args["task_id"]

    if task_id not in _task_log:
        return f"Error: task '{task_id}' not found."

    status = _task_log[task_id]["status"]
    if status != "scheduled":
        return f"Cannot cancel task '{task_id}' — it is already '{status}'."

    try:
        _scheduler.remove_job(task_id)
        _task_log[task_id]["status"] = "cancelled"
        _save_task_log()
        return f"Task '{task_id}' cancelled successfully."
    except Exception as e:
        return f"Error cancelling task: {e}"


# ── Job Auto-Apply Tools ───────────────────────────────────────────────────────
#
# These tools let Orion search for jobs on Greenhouse and Lever (two ATS platforms
# used by hundreds of tech companies) and auto-apply using Playwright.
#
# Before using: fill in job_apply/profile.json with your personal details.

import os as _os

_PROFILE_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "job_apply", "profile.json")
_APPLIED_LOG  = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "job_apply", "applied_jobs.json")


def _load_profile() -> dict:
    if _os.path.exists(_PROFILE_PATH):
        with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_applied_log() -> list:
    if _os.path.exists(_APPLIED_LOG):
        with open(_APPLIED_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_applied_log(log: list):
    with open(_APPLIED_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


@registry.tool(
    name="search_jobs",
    description=(
        "Search for job listings on Greenhouse and Lever — two ATS platforms used by "
        "hundreds of tech companies including Stripe, Airbnb, Netflix, Figma, Spotify, and more. "
        "Returns a list of job URLs with title, company, and description. "
        "Use apply_job to apply to any result. "
        "ATS filter options: 'greenhouse', 'lever', or 'all' (default)."
    ),
    properties={
        "query": {
            "type": "string",
            "description": "Job title or keywords, e.g. 'machine learning engineer' or 'data scientist'",
        },
        "location": {
            "type": "string",
            "description": "Optional city or region, e.g. 'Seattle', 'New York', 'remote'. Leave empty for all locations.",
        },
        "ats": {
            "type": "string",
            "enum": ["all", "greenhouse", "lever"],
            "description": "Which ATS platform to search. Default: 'all'",
        },
        "max_results": {
            "type": "integer",
            "description": "Max job listings to return (default: 10, max: 20)",
        },
    },
    required=["query"],
)
async def search_jobs(args: dict) -> str:
    from job_apply.detector import build_search_queries, parse_job_urls
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    query      = args["query"]
    location   = args.get("location", "")
    ats        = args.get("ats", "all")
    max_res    = min(int(args.get("max_results", 10)), 20)

    queries = build_search_queries(query, location, ats)
    all_results = []

    try:
        with DDGS() as ddgs:
            for q in queries:
                for r in ddgs.text(q, max_results=15):
                    all_results.append({
                        "url":     r.get("href", ""),
                        "title":   r.get("title", ""),
                        "snippet": r.get("body", ""),
                    })
    except Exception as e:
        return f"Search error: {e}"

    jobs = parse_job_urls(all_results, ats)[:max_res]

    if not jobs:
        return f"No {ats} job listings found for '{query}' {location}. Try different keywords or remove the location filter."

    return json.dumps({
        "count": len(jobs),
        "query": query,
        "location": location,
        "jobs": jobs,
    }, indent=2)


@registry.tool(
    name="apply_job",
    description=(
        "Auto-apply to a job on Greenhouse or Lever using your saved profile (job_apply/profile.json). "
        "Opens a real browser window so you can monitor the process. "
        "Fills in your name, email, phone, LinkedIn, resume, and standard answers to common questions. "
        "Returns success/failure status with any error details. "
        "Make sure profile.json is filled in before calling this tool. "
        "Supported URLs: boards.greenhouse.io/... and jobs.lever.co/..."
    ),
    properties={
        "url": {
            "type": "string",
            "description": "Full job application URL, e.g. https://boards.greenhouse.io/stripe/jobs/12345",
        },
        "job_title": {
            "type": "string",
            "description": "Job title for the application log (optional but helpful for tracking).",
        },
        "company": {
            "type": "string",
            "description": "Company name for the log (optional).",
        },
    },
    required=["url"],
)
async def apply_job(args: dict) -> str:
    from job_apply.detector import detect_ats

    url       = args["url"]
    job_title = args.get("job_title", "Unknown role")
    company   = args.get("company", "")

    ats = detect_ats(url)
    if not ats:
        return f"Error: '{url}' is not a supported Greenhouse or Lever URL."

    profile = _load_profile()

    # Basic profile validation
    missing = [f for f in ["first_name", "email", "resume_path"] if not profile.get(f)]
    if missing:
        return (
            f"Profile incomplete. Fill in these fields in job_apply/profile.json first: {', '.join(missing)}\n"
            f"Profile file: {_PROFILE_PATH}"
        )

    if not _os.path.exists(profile.get("resume_path", "")):
        return (
            f"Resume not found at: {profile.get('resume_path')}.\n"
            "Update 'resume_path' in job_apply/profile.json with the full path to your PDF resume."
        )

    # Run the appropriate applier in a thread (sync Playwright avoids Windows asyncio subprocess error)
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    if ats == "greenhouse":
        from job_apply.greenhouse import apply_greenhouse_sync
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = await loop.run_in_executor(executor, apply_greenhouse_sync, url, profile)
    else:
        from job_apply.lever import apply_lever_sync
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = await loop.run_in_executor(executor, apply_lever_sync, url, profile)

    # Log the application attempt
    log = _load_applied_log()
    import datetime as _dt
    log.append({
        "url":       url,
        "ats":       ats,
        "company":   company,
        "job_title": job_title,
        "applied_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "success":   result.get("success"),
        "message":   result.get("message"),
    })
    _save_applied_log(log)

    return json.dumps(result, indent=2)


@registry.tool(
    name="list_applications",
    description="Show the history of all job applications submitted by Orion — company, role, date, and success/failure status.",
    properties={},
    required=[],
)
async def list_applications(args: dict) -> str:
    log = _load_applied_log()
    if not log:
        return "No applications submitted yet. Use search_jobs to find jobs and apply_job to apply."
    return json.dumps(log, indent=2)
