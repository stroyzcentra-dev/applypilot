"""Hard filters: cheap, deterministic, explainable. Run before any LLM call.

Each filter returns a reason string when it rejects a posting, so the log
always says *why* a job was skipped.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from applypilot.models import JobPosting, Preferences


def _contains_word(haystack: str, needle: str) -> bool:
    return re.search(rf"\b{re.escape(needle.lower())}\b", haystack.lower()) is not None


def title_reason(job: JobPosting, prefs: Preferences) -> str | None:
    for bad in prefs.avoid_title_keywords:
        if _contains_word(job.title, bad):
            return f"title contains avoided keyword '{bad}'"
    if not any(_title_matches(job.title, t) for t in prefs.target_titles):
        return "title doesn't match any target title"
    return None


def _title_matches(title: str, target: str) -> bool:
    """Every word of the target title appears in the posting title, in any order.

    'backend engineer' matches 'Senior Software Engineer, Backend'.
    """
    return all(_contains_word(title, word) for word in target.split())


def location_reason(job: JobPosting, prefs: Preferences) -> str | None:
    if job.remote and prefs.remote_ok:
        return None
    if not prefs.locations:
        return None
    if any(_contains_word(job.location, loc) for loc in prefs.locations):
        return None
    return f"location '{job.location or 'unknown'}' isn't in preferred locations"


def company_reason(job: JobPosting, prefs: Preferences) -> str | None:
    excluded = {c.lower() for c in prefs.exclude_companies}
    return f"company '{job.company}' is excluded" if job.company.lower() in excluded else None


def salary_reason(job: JobPosting, prefs: Preferences) -> str | None:
    # Only reject when the posting states a max below the floor. Unknown salary passes.
    floor = prefs.min_base_salary_usd
    if floor and job.salary_max_usd and job.salary_max_usd < floor:
        return f"salary max ${job.salary_max_usd:,} is below floor ${floor:,}"
    return None


def age_reason(job: JobPosting, prefs: Preferences, now: datetime | None = None) -> str | None:
    if not prefs.max_posting_age_days or not job.posted_at:
        return None
    now = now or datetime.now(UTC)
    if now - job.posted_at > timedelta(days=prefs.max_posting_age_days):
        return f"posted more than {prefs.max_posting_age_days} days ago"
    return None


FILTERS = (company_reason, title_reason, location_reason, salary_reason, age_reason)


def rejection_reason(job: JobPosting, prefs: Preferences) -> str | None:
    """First reason this job fails the hard filters, or None if it passes."""
    for check in FILTERS:
        if reason := check(job, prefs):
            return reason
    return None
