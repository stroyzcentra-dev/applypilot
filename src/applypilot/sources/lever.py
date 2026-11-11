"""Lever public Postings API.

Docs: https://github.com/lever/postings-api
"""

from __future__ import annotations

import httpx

from applypilot.models import JobPosting
from applypilot.sources._text import from_epoch_ms, html_to_text, looks_remote

BASE = "https://api.lever.co/v0/postings/{token}"


async def fetch(client: httpx.AsyncClient, token: str) -> list[JobPosting]:
    resp = await client.get(BASE.format(token=token), params={"mode": "json"})
    resp.raise_for_status()
    return [parse(token, job) for job in resp.json()]


def _salary(job: dict) -> tuple[int | None, int | None]:
    rng = job.get("salaryRange") or {}
    if rng.get("currency") != "USD" or rng.get("interval") not in (None, "per-year-salary"):
        return None, None
    return rng.get("min"), rng.get("max")


def parse(token: str, job: dict) -> JobPosting:
    cats = job.get("categories") or {}
    location = cats.get("location") or ""
    parts = [job.get("descriptionPlain") or html_to_text(job.get("description"))]
    for section in job.get("lists") or []:
        parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content'))}")
    if job.get("additionalPlain"):
        parts.append(job["additionalPlain"])
    lo, hi = _salary(job)
    return JobPosting(
        source="lever",
        external_id=job["id"],
        company=token,
        title=job["text"].strip(),
        url=job["hostedUrl"],
        apply_url=job.get("applyUrl"),
        location=location,
        remote=job.get("workplaceType") == "remote" or looks_remote(location),
        description="\n\n".join(p.strip() for p in parts if p and p.strip()),
        department=cats.get("team"),
        posted_at=from_epoch_ms(job.get("createdAt")),
        salary_min_usd=lo,
        salary_max_usd=hi,
    )
