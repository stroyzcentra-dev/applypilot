"""Ashby public Job Posting API.

Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
"""

from __future__ import annotations

import httpx

from applypilot.models import JobPosting
from applypilot.sources._text import html_to_text, looks_remote, parse_iso

BASE = "https://api.ashbyhq.com/posting-api/job-board/{token}"


async def fetch(client: httpx.AsyncClient, token: str) -> list[JobPosting]:
    resp = await client.get(BASE.format(token=token), params={"includeCompensation": "true"})
    resp.raise_for_status()
    return [parse(token, job) for job in resp.json()["jobs"] if job.get("isListed", True)]


def _salary(job: dict) -> tuple[int | None, int | None]:
    comp = job.get("compensation") or {}
    for part in comp.get("summaryComponents") or []:
        if (
            part.get("compensationType") == "Salary"
            and part.get("currencyCode") == "USD"
            and part.get("interval") == "1 YEAR"
        ):
            lo, hi = part.get("minValue"), part.get("maxValue")
            return (int(lo) if lo else None), (int(hi) if hi else None)
    return None, None


def _is_remote(job: dict, locations: list[str]) -> bool:
    # `isRemote` is unreliable in practice (it's true on many hybrid roles), so trust
    # `workplaceType` when present and fall back to the flag only when it's missing.
    kind = (job.get("workplaceType") or "").lower()
    if kind:
        return kind == "remote" or looks_remote(*locations)
    return bool(job.get("isRemote")) or looks_remote(*locations)


def parse(token: str, job: dict) -> JobPosting:
    locations = [job.get("location") or ""]
    locations += [s.get("location", "") for s in job.get("secondaryLocations") or []]
    locations = [loc for loc in locations if loc]
    lo, hi = _salary(job)
    return JobPosting(
        source="ashby",
        external_id=job["id"],
        company=token,
        title=job["title"].strip(),
        url=job["jobUrl"],
        apply_url=job.get("applyUrl"),
        location="; ".join(locations),
        remote=_is_remote(job, locations),
        description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
        department=job.get("department"),
        posted_at=parse_iso(job.get("publishedAt")),
        salary_min_usd=lo,
        salary_max_usd=hi,
    )
