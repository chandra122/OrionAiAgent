"""
lever.py — Auto-apply to Lever ATS job postings using Playwright (sync API).

Uses sync_playwright to avoid Windows asyncio subprocess restrictions.
Called from tools.py via asyncio.get_event_loop().run_in_executor() so it
runs in a thread without blocking the FastAPI event loop.

Form structure (consistent across all Lever companies):
    input[name="name"]            — full name
    input[name="email"]           — email
    input[name="phone"]           — phone
    input[name="org"]             — current company
    input[name="urls[LinkedIn]"]  — LinkedIn URL
    input[name="urls[GitHub]"]    — GitHub URL
    input[name="urls[Portfolio]"] — portfolio URL
    input[type="file"]            — resume upload
    textarea                      — cover letter (if present)
    Custom questions              — vary by company
"""

import os
import time
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout


def apply_lever_sync(url: str, profile: dict) -> dict:
    """
    Fill and submit a Lever job application (synchronous, runs in a thread).

    Returns:
        dict with keys: success (bool), message (str), url (str), screenshot (str|None)
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)

            apply_btn = page.locator('a.template-btn-submit, a:has-text("Apply for this job"), button:has-text("Apply")')
            if apply_btn.count() > 0:
                apply_btn.first.click()
                page.wait_for_timeout(2000)

            # ── Core fields ────────────────────────────────────────────────────
            full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip() or profile.get("full_name", "")
            _fill_if_exists(page, 'input[name="name"]',  full_name)
            _fill_if_exists(page, 'input[name="email"]', profile.get("email", ""))
            _fill_if_exists(page, 'input[name="phone"]', profile.get("phone", ""))
            _fill_if_exists(page, 'input[name="org"]',   profile.get("current_company", ""))

            # ── Social URLs ────────────────────────────────────────────────────
            _fill_if_exists(page, 'input[name="urls[LinkedIn]"]',  profile.get("linkedin_url", ""))
            _fill_if_exists(page, 'input[name="urls[GitHub]"]',    profile.get("github_url", ""))
            _fill_if_exists(page, 'input[name="urls[Portfolio]"]', profile.get("portfolio_url", ""))
            _fill_if_exists(page, 'input[name="urls[Other]"]',     profile.get("portfolio_url", ""))

            # ── Resume upload ──────────────────────────────────────────────────
            resume_path = profile.get("resume_path", "")
            if resume_path and os.path.exists(resume_path):
                file_input = page.locator('input[type="file"]').first
                if file_input.count() > 0:
                    file_input.set_input_files(resume_path)
                    page.wait_for_timeout(1000)

            # ── Cover letter ───────────────────────────────────────────────────
            cover = page.locator('textarea[name="comments"], textarea[placeholder*="cover"], .cover-letter textarea')
            if cover.count() > 0:
                cover.first.fill(profile.get("standard_answers", {}).get("why_interested", ""))

            # ── Custom questions ───────────────────────────────────────────────
            _handle_custom_questions(page, profile)

            # ── Submit ─────────────────────────────────────────────────────────
            submit = page.locator('button[type="submit"], input[type="submit"]').last
            if submit.count() == 0:
                return {"success": False, "message": "Submit button not found.", "url": url, "screenshot": None}

            screenshot = _take_screenshot(page, "pre_submit")
            submit.click()
            page.wait_for_timeout(3000)

            body_text = page.inner_text("body").lower()
            success_signals = [
                "application submitted",
                "thank you for applying",
                "thanks for applying",
                "we've received your application",
                "application received",
                "successfully submitted",
            ]
            succeeded = any(s in body_text for s in success_signals)
            post_screenshot = _take_screenshot(page, "post_submit")

            if succeeded:
                return {
                    "success": True,
                    "message": "Application submitted successfully.",
                    "url": url,
                    "screenshot": post_screenshot,
                }
            else:
                errors = page.locator(".error-message, [class*='error'], .field-error").all_text_contents()
                error_msg = "; ".join(e.strip() for e in errors if e.strip()) or "Unknown — check screenshot."
                return {
                    "success": False,
                    "message": f"Submission may have failed. Errors: {error_msg}",
                    "url": url,
                    "screenshot": post_screenshot,
                }

        except PlaywrightTimeout:
            return {"success": False, "message": "Page timed out loading.", "url": url, "screenshot": None}
        except Exception as e:
            return {"success": False, "message": f"Error: {e}", "url": url, "screenshot": None}
        finally:
            page.wait_for_timeout(2000)
            browser.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fill_if_exists(page: Page, selector: str, value: str):
    if not value:
        return
    loc = page.locator(selector)
    if loc.count() > 0:
        loc.first.fill(value)
        page.wait_for_timeout(100)


def _handle_custom_questions(page: Page, profile: dict):
    answers = profile.get("standard_answers", {})
    questions = page.locator(".application-question")
    count = questions.count()

    for i in range(count):
        q = questions.nth(i)
        label_el = q.locator("label, legend").first
        label_text = label_el.inner_text().lower() if label_el.count() > 0 else ""

        text_inp = q.locator("input[type='text'], input[type='number']")
        if text_inp.count() > 0:
            answer = _match_answer(label_text, answers)
            if answer:
                text_inp.first.fill(answer)
                page.wait_for_timeout(80)
            continue

        textarea = q.locator("textarea")
        if textarea.count() > 0:
            answer = _match_answer(label_text, answers)
            if answer:
                textarea.first.fill(answer)
                page.wait_for_timeout(80)
            continue

        select = q.locator("select")
        if select.count() > 0:
            options = select.locator("option").all_text_contents()
            chosen = _pick_dropdown_option(label_text, options, answers)
            if chosen:
                select.select_option(label=chosen)
                page.wait_for_timeout(80)
            continue

        radios = q.locator("input[type='radio']")
        if radios.count() > 0:
            if any(k in label_text for k in ["sponsor", "visa", "require"]):
                no_radio = q.locator("label:has-text('No') input, input[value='No']")
                if no_radio.count() > 0:
                    no_radio.first.click()
            else:
                yes_radio = q.locator("label:has-text('Yes') input, input[value='Yes']")
                if yes_radio.count() > 0:
                    yes_radio.first.click()
            page.wait_for_timeout(80)


def _match_answer(label: str, answers: dict) -> str:
    label = label.lower()
    if any(k in label for k in ["salary", "compensation", "pay", "expect"]):
        return answers.get("salary_expectation", "")
    if any(k in label for k in ["notice", "start", "available", "when"]):
        return answers.get("notice_period", "2 weeks")
    if any(k in label for k in ["hear", "source", "find", "refer"]):
        return answers.get("how_did_you_hear", "Job board")
    if any(k in label for k in ["why", "interest", "motivat", "tell us"]):
        return answers.get("why_interested", "")
    if any(k in label for k in ["strength", "skill", "experience"]):
        return answers.get("greatest_strength", "")
    return ""


def _pick_dropdown_option(label: str, options: list[str], answers: dict) -> str | None:
    label = label.lower()
    clean_opts = [o.strip() for o in options if o.strip() and o.strip().lower() not in ("select", "-- select --", "")]
    if not clean_opts:
        return None
    if any(k in label for k in ["authorized", "eligible", "work in"]):
        for opt in clean_opts:
            if "yes" in opt.lower():
                return opt
    if any(k in label for k in ["sponsor", "visa"]):
        for opt in clean_opts:
            if "no" in opt.lower():
                return opt
    if "relocat" in label:
        for opt in clean_opts:
            if "no" in opt.lower():
                return opt
    if any(k in label for k in ["hear", "source"]):
        return clean_opts[0]
    return None


def _take_screenshot(page: Page, suffix: str) -> str | None:
    try:
        dir_path = os.path.dirname(__file__)
        path = os.path.join(dir_path, f"screenshot_{suffix}_{int(time.time())}.png")
        page.screenshot(path=path, full_page=False)
        return path
    except Exception:
        return None
