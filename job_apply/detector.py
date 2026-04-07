"""
detector.py — Detect which ATS platform a URL belongs to and search for jobs.

Supported platforms:
    Greenhouse  — boards.greenhouse.io/{company}/jobs/{id}
    Lever       — jobs.lever.co/{company}/{id}

search_jobs() uses DuckDuckGo site: search to find real job listings
on these platforms without needing any API keys.
"""

import re
from urllib.parse import urlparse


# ── ATS detection ─────────────────────────────────────────────────────────────

def detect_ats(url: str) -> str | None:
    """
    Return 'greenhouse', 'lever', or None if not a known ATS.

    Examples:
        https://boards.greenhouse.io/stripe/jobs/12345  → 'greenhouse'
        https://jobs.lever.co/airbnb/abc-def-123        → 'lever'
        https://linkedin.com/jobs/view/123              → None
    """
    url = url.strip().lower()
    if "boards.greenhouse.io" in url or "greenhouse.io" in url:
        return "greenhouse"
    if "jobs.lever.co" in url or "lever.co" in url:
        return "lever"
    return None


def extract_company(url: str) -> str:
    """
    Extract company slug from a Greenhouse or Lever URL.

    boards.greenhouse.io/stripe/jobs/123  → "stripe"
    jobs.lever.co/airbnb/abc-123          → "airbnb"
    """
    parts = urlparse(url).path.strip("/").split("/")
    if parts:
        return parts[0]
    return "unknown"


# ── Job search via DuckDuckGo site: operator ──────────────────────────────────

def build_search_queries(query: str, location: str = "", ats: str = "all") -> list[str]:
    """
    Build DuckDuckGo search queries targeting Greenhouse and/or Lever job boards.

    Args:
        query    — job title or keywords, e.g. "machine learning engineer"
        location — optional city/region, e.g. "Seattle" or "remote"
        ats      — "greenhouse", "lever", or "all"

    Returns:
        List of search query strings to run through web_search tool.
    """
    # Location appended as plain keyword (not in site: query — DDG ignores geo inside site: searches)
    loc = f" {location}" if location else ""
    queries = []

    if ats in ("greenhouse", "all"):
        queries.append(f'site:boards.greenhouse.io {query}{loc}')

    if ats in ("lever", "all"):
        queries.append(f'site:jobs.lever.co {query}{loc}')

    return queries


def parse_job_urls(search_results: list[dict], ats_filter: str = "all") -> list[dict]:
    """
    Filter search results to only job listing URLs on known ATS platforms.

    Args:
        search_results — list of dicts from web_search: [{title, url, snippet}]
        ats_filter     — "greenhouse", "lever", or "all"

    Returns:
        List of dicts: [{url, title, company, ats, snippet}]
    """
    jobs = []
    seen = set()

    for r in search_results:
        url     = r.get("url", "")
        title   = r.get("title", "")
        snippet = r.get("snippet", "")

        ats = detect_ats(url)
        if not ats:
            continue
        if ats_filter != "all" and ats != ats_filter:
            continue

        # Deduplicate by URL
        clean_url = url.split("?")[0].rstrip("/")
        if clean_url in seen:
            continue

        # Skip non-job pages (company root, search pages)
        path = urlparse(url).path.strip("/")
        path_parts = path.split("/")

        if ats == "greenhouse" and len(path_parts) < 3:
            continue   # needs /company/jobs/id
        if ats == "lever" and len(path_parts) < 2:
            continue   # needs /company/id

        seen.add(clean_url)
        jobs.append({
            "url":     url,
            "title":   title,
            "company": extract_company(url),
            "ats":     ats,
            "snippet": snippet,
        })

    return jobs
