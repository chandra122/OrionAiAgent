"""
main.py - Entry point for Orion.

Usage:
    python main.py          # shows menu to choose what to run
    python main.py --chat   # jump straight into chat mode
"""

import anyio
import sys
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env file into os.environ

from tools import registry
from agent import Agent


SYSTEM_PROMPT = """You are Orion, a capable AI agent. You have access to the following tools:

- get_datetime      : Get the current date and time
- calculate         : Safely evaluate a mathematical expression
- get_weather       : Get live weather for any city (no API key needed)
- web_search        : Search the web with DuckDuckGo (no API key needed)
- read_file         : Read any file from disk
- write_file        : Write content to a file on disk
- run_python        : Execute Python code in a subprocess and return the output
- schedule_task     : Schedule a task to run at a future time
- list_tasks        : List all scheduled tasks
- cancel_task       : Cancel a scheduled task
- search_jobs       : Search for job listings on Greenhouse and Lever ATS platforms
- apply_job         : Auto-apply to a Greenhouse or Lever job using the saved profile
- list_applications : Show history of all submitted job applications

JOB APPLY WORKFLOW:
1. Use search_jobs to find relevant listings (e.g. "machine learning engineer", "Seattle")
2. Present the results to the user and ask which ones to apply to
3. For each confirmed job, call apply_job with the URL, job_title, and company
4. Report success/failure for each application
5. Always confirm before applying — never apply without user approval

RULES:
- Think step-by-step before choosing a tool.
- Use tools for real-time data (weather, news, dates). Do not guess.
- When writing files, confirm by reading them back.
- When running code, explain what the code does before running it.
- Summarize findings clearly at the end.
- Do not use emojis in any output.
- Never apply to a job without explicit user confirmation.
"""


def build_agent(verbose: bool = True) -> Agent:
    return Agent(
        registry=registry,
        model="claude-opus-4-6",
        system_prompt=SYSTEM_PROMPT,
        max_iterations=50,
        verbose=verbose,
    )


# ── Runners ────────────────────────────────────────────────────────────────────

async def run_datetime():
    agent = build_agent()
    result = await agent.run("Get the current date and time using get_datetime.")
    print("\nResult:", result)


async def run_calculator():
    expr = input("Enter math expression (e.g. 1234 * 5678 + 99**2): ").strip()
    agent = build_agent()
    result = await agent.run(f"Calculate this expression using the calculate tool: {expr}")
    print("\nResult:", result)


async def run_weather():
    city = input("Enter city name: ").strip()
    agent = build_agent()
    result = await agent.run(f"Get the current weather for {city} using get_weather.")
    print("\nResult:", result)


async def run_web_search():
    query = input("Enter search query: ").strip()
    agent = build_agent()
    result = await agent.run(f"Search the web for: {query}")
    print("\nResult:", result)


async def run_python_code():
    print("Enter Python code to run (type END on a new line to finish):")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    code = "\n".join(lines)
    agent = build_agent()
    result = await agent.run(f"Run this Python code using run_python:\n\n{code}")
    print("\nResult:", result)


async def run_file_ops():
    print("1. Read a file")
    print("2. Write a file")
    choice = input("Choose (1/2): ").strip()
    agent = build_agent()
    if choice == "1":
        path = input("File path to read: ").strip()
        result = await agent.run(f"Read the file at: {path}")
    else:
        path = input("File path to write: ").strip()
        content = input("Content to write: ").strip()
        result = await agent.run(f"Write this content to {path}:\n\n{content}")
    print("\nResult:", result)


async def run_custom_prompt():
    prompt = input("Enter your prompt: ").strip()
    agent = build_agent()
    result = await agent.run(prompt)
    print("\nResult:", result)


async def run_full_demo():
    agent = build_agent()
    prompt = (
        "Complete these tasks and report results without using emojis:\n"
        "1. Get the current date and time.\n"
        "2. Calculate: (1234 * 5678) + (99 ** 2)\n"
        "3. Run Python code that generates the first 10 prime numbers.\n"
        "4. Get live weather for Tokyo.\n"
        "5. Search the web for 'Agentic AI 2025 trends' and summarize the top result.\n"
        "6. Write a plain Markdown report to demo_report.md with all results.\n"
        "7. Read demo_report.md back to confirm it saved correctly."
    )
    result = await agent.run(prompt)
    print("\nResult:", result)


async def run_chat():
    agent = build_agent(verbose=False)
    print("\nChat mode - type 'exit' to quit, 'reset' to clear history.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        if user_input.lower() == "reset":
            agent.reset()
            print("[History cleared]\n")
            continue

        if not user_input:
            continue

        result = await agent.run(user_input)
        print(f"\nAgent: {result}\n")


# ── Menu ───────────────────────────────────────────────────────────────────────

async def run_list_tasks():
    """Directly print the task log without going through the agent."""
    from tools import _task_log, _TASKS_FILE
    if not _task_log:
        print("No tasks found.")
        return
    print(f"Tasks file: {_TASKS_FILE}\n")
    for t in _task_log.values():
        status = t["status"].upper()
        print(f"  [{status}] {t['task_id']}")
        print(f"    Action : {t['action']}")
        print(f"    Run at : {t['run_at']}")
        print(f"    Data   : {t['data']}")
        if t.get("output"):
            print(f"    Output : {t['output'][:200]}")
        print()


MENU = {
    "1": ("Get current date & time",     run_datetime),
    "2": ("Calculator",                   run_calculator),
    "3": ("Live weather for a city",      run_weather),
    "4": ("Web search",                   run_web_search),
    "5": ("Run Python code",              run_python_code),
    "6": ("Read / Write a file",          run_file_ops),
    "7": ("Custom prompt",                run_custom_prompt),
    "8": ("Full demo (all tools)",        run_full_demo),
    "9": ("Chat mode (multi-turn)",       run_chat),
    "10": ("List scheduled tasks",        run_list_tasks),
}


async def main():
    if "--chat" in sys.argv or "-c" in sys.argv:
        await run_chat()
        return

    while True:
        print("\nOrion")
        print(f"Tools: {', '.join(registry.list_tools())}\n")

        for key, (label, _) in MENU.items():
            print(f"  {key}. {label}")
        print("  0. Exit")
        print()

        try:
            choice = input("Choose an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if choice == "0":
            print("Goodbye!")
            break

        if choice not in MENU:
            print("Invalid choice. Try again.")
            continue

        label, fn = MENU[choice]
        print(f"\n--- {label} ---\n")
        await fn()
        input("\nPress Enter to return to menu...")


if __name__ == "__main__":
    anyio.run(main)
