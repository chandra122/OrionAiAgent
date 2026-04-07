"""
greenhouse.py — Auto-apply to Greenhouse ATS job postings using Playwright (sync API).

Uses sync_playwright to avoid Windows asyncio subprocess restrictions.
Called from tools.py via asyncio.get_event_loop().run_in_executor() so it
runs in a thread without blocking the FastAPI event loop.

Form structure (consistent across all Greenhouse companies):
    #first_name       — required
    #last_name        — required
    #email            — required
    #phone            — optional
    #resume           — file upload (PDF)
    #cover_letter_text — optional textarea
    #website          — portfolio / GitHub URL
    #linkedin_profile — LinkedIn URL
    Custom questions  — vary by company (text, select, yes/no)
"""

import os
import time
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout


def apply_greenhouse_sync(url: str, profile: dict) -> dict:
    """
    Fill and submit a Greenhouse job application (synchronous, runs in a thread).

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

            # Some Greenhouse pages have an "Apply for this job" button first
            apply_btn = page.locator('a:has-text("Apply for this job"), button:has-text("Apply")')
            if apply_btn.count() > 0:
                apply_btn.first.click()
                page.wait_for_timeout(1500)

            # ── Fill core fields ───────────────────────────────────────────────
            _fill_if_exists(page, "#first_name", profile.get("first_name", ""))
            _fill_if_exists(page, "#last_name",  profile.get("last_name", ""))
            _fill_if_exists(page, "#email",      profile.get("email", ""))
            _fill_if_exists(page, "#phone",      profile.get("phone", ""))

            _fill_if_exists(page, "#linkedin_profile", profile.get("linkedin_url", ""))
            _fill_if_exists(page, "#website",          profile.get("portfolio_url", "") or profile.get("github_url", ""))

            # ── Resume upload ──────────────────────────────────────────────────
            resume_path = profile.get("resume_path", "")
            if resume_path and os.path.exists(resume_path):
                resume_input = page.locator('input[type="file"]').first
                if resume_input.count() > 0:
                    resume_input.set_input_files(resume_path)
                    page.wait_for_timeout(800)

            # ── Cover letter ───────────────────────────────────────────────────
            cover = page.locator("#cover_letter_text, textarea[name*='cover']")
            if cover.count() > 0:
                cover.first.fill(profile.get("standard_answers", {}).get("why_interested", ""))

            # ── Custom questions ───────────────────────────────────────────────
            _handle_custom_questions(page, profile)

            # ── Submit ─────────────────────────────────────────────────────────
            submit = page.locator('input[type="submit"], button[type="submit"]').last
            if submit.count() == 0:
                return {"success": False, "message": "Submit button not found", "url": url, "screenshot": None}

            screenshot = _take_screenshot(page, "pre_submit")
            submit.click()
            page.wait_for_timeout(3000)

            body_text = page.inner_text("body").lower()
            success_signals = [
                "application submitted",
                "thank you for applying",
                "thanks for applying",
                "we have received your application",
                "application received",
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
                errors = page.locator(".error, .field_with_errors, [class*='error']").all_text_contents()
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

    # Text/textarea custom questions
    custom_inputs = page.locator(".custom-question input[type='text'], .custom-question textarea")
    count = custom_inputs.count()
    for i in range(count):
        inp = custom_inputs.nth(i)
        label_el = page.locator(f"label[for='{inp.get_attribute('id')}']")
        label_text = ""
        if label_el.count() > 0:
            label_text = label_el.inner_text().lower()
        answer = _match_answer(label_text, answers)
        if answer:
            inp.fill(answer)
            page.wait_for_timeout(80)

    # Select dropdowns
    selects = page.locator(".custom-question select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        options = sel.locator("option").all_text_contents()
        label_el_id = sel.get_attribute("id")
        label_text = ""
        if label_el_id:
            lbl = page.locator(f"label[for='{label_el_id}']")
            if lbl.count() > 0:
                label_text = lbl.inner_text().lower()
        chosen = _pick_dropdown_option(label_text, options, answers)
        if chosen:
            sel.select_option(label=chosen)
            page.wait_for_timeout(80)

    # Yes/No radio buttons
    radio_groups = page.locator(".custom-question .radio-group, .custom-question [role='radiogroup']")
    for i in range(radio_groups.count()):
        group = radio_groups.nth(i)
        label_text = group.locator("legend, label").first.inner_text().lower() if group.locator("legend, label").count() > 0 else ""
        if any(k in label_text for k in ["sponsor", "visa", "authorization"]):
            no_radio = group.locator("input[value='No'], label:has-text('No')")
            if no_radio.count() > 0:
                no_radio.first.click()
        else:
            yes_radio = group.locator("input[value='Yes'], label:has-text('Yes')")
            if yes_radio.count() > 0:
                yes_radio.first.click()
        page.wait_for_timeout(80)


def _match_answer(label: str, answers: dict) -> str:
    label = label.lower()
    if any(k in label for k in ["salary", "compensation", "pay"]):
        return answers.get("salary_expectation", "")
    if any(k in label for k in ["notice", "start", "available", "when can"]):
        return answers.get("notice_period", "2 weeks")
    if any(k in label for k in ["hear", "source", "find", "refer"]):
        return answers.get("how_did_you_hear", "Job board")
    if any(k in label for k in ["why", "interest", "motivat"]):
        return answers.get("why_interested", "")
    if any(k in label for k in ["strength", "skill"]):
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
    if any(k in label for k in ["hear", "source", "refer"]):
        return clean_opts[0] if clean_opts else None
    return None


def _take_screenshot(page: Page, suffix: str) -> str | None:
    try:
        dir_path = os.path.dirname(__file__)
        path = os.path.join(dir_path, f"screenshot_{suffix}_{int(time.time())}.png")
        page.screenshot(path=path, full_page=False)
        return path
    except Exception:
        return None
