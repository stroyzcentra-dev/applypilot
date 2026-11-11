"""Turn a TailoredResume into a one-page PDF.

The layout is deliberately plain: one column, real text, standard section names,
no tables or icons. That's what applicant tracking systems parse reliably.

PDFs are printed by Chromium (the desktop container's, or a local headless one)
in a throwaway browser context, so rendering never touches an application's tabs.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from playwright.async_api import async_playwright

from applypilot.config import AutomationSettings
from applypilot.tailor.models import TailoredResume
from applypilot.tailor.tailor import drop_least_relevant

_PAGE = re.compile(rb"/Type\s*/Page(?!s)")
MAX_TRIMS = 200  # safety bound only; the loop ends when nothing is left to drop

CSS = """
@page { size: Letter; margin: 0.55in 0.6in; }
* { box-sizing: border-box; }
body { font-family: "Liberation Sans", Arial, Helvetica, sans-serif; font-size: 10.5pt;
       line-height: 1.35; color: #111; margin: 0; }
h1 { font-size: 20pt; margin: 0 0 2pt; letter-spacing: -0.01em; }
.contact { font-size: 9.5pt; color: #333; margin-bottom: 10pt; }
.contact span + span::before { content: " | "; color: #888; }
h2 { font-size: 10.5pt; margin: 12pt 0 4pt; padding-bottom: 2pt; border-bottom: 1px solid #999; }
.summary { margin: 0; }
.role { margin-bottom: 6pt; }
.role-head { display: flex; justify-content: space-between; gap: 12pt; }
.role-head strong { font-size: 10.5pt; }
.dates { color: #333; white-space: nowrap; }
ul { margin: 2pt 0 0; padding-left: 14pt; }
li { margin: 0 0 2pt; }
.skills { margin: 0; }
a { color: inherit; text-decoration: none; }
"""


def to_html(r: TailoredResume) -> str:
    e = html.escape
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(r.name)}</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>{e(r.name)}</h1>",
             "<div class='contact'>" + "".join(f"<span>{e(c)}</span>" for c in r.contact) + "</div>"]
    if r.summary:
        parts.append(f"<h2>Summary</h2><p class='summary'>{e(r.summary)}</p>")
    if r.roles:
        parts.append("<h2>Experience</h2>")
        for role in r.roles:
            head = e(role.org) + (f", {e(role.title)}" if role.title else "")
            parts.append(f"<div class='role'><div class='role-head'><strong>{head}</strong>"
                         f"<span class='dates'>{e(role.dates or '')}</span></div>")
            if role.bullets:
                parts.append("<ul>" + "".join(f"<li>{e(b.text)}</li>" for b in role.bullets) + "</ul>")
            parts.append("</div>")
    if r.projects:
        parts.append("<h2>Projects</h2><ul>")
        for p in r.projects:
            link = f" ({e(p.link)})" if p.link else ""
            parts.append(f"<li>{e(p.bullet.text)}{link}</li>")
        parts.append("</ul>")
    if r.education:
        parts.append("<h2>Education</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in r.education) + "</ul>")
    if r.skills:
        parts.append(f"<h2>Skills</h2><p class='skills'>{e(', '.join(r.skills))}</p>")
    parts.append("</body></html>")
    return "".join(parts)


def page_count(pdf: bytes) -> int:
    return len(_PAGE.findall(pdf))


async def render_pdf(resume: TailoredResume, settings: AutomationSettings) -> tuple[bytes, int]:
    """Print to PDF, dropping least-relevant bullets until it fits on one page.

    Returns the PDF and how many bullets were dropped to fit.
    """
    async with async_playwright() as pw:
        if settings.backend == "docker":
            browser = await pw.chromium.connect_over_cdp(settings.cdp_url)
        else:
            browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        try:
            page = await ctx.new_page()
            dropped = 0
            while True:
                await page.set_content(to_html(resume), wait_until="load")
                pdf = await page.pdf(format="Letter", print_background=True,
                                     prefer_css_page_size=True)
                if page_count(pdf) <= 1 or dropped >= MAX_TRIMS or not drop_least_relevant(resume):
                    return pdf, dropped
                dropped += 1
        finally:
            await ctx.close()
            if settings.backend == "local":
                await browser.close()


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:120]


def resume_path(base: Path, job_id: str, name: str) -> Path:
    """One folder per job; the file itself is named for the person, since recruiters see it.

    data/resumes/<job>/Jordan_Example_Resume.pdf
    """
    return base / _safe(job_id) / f"{_safe(name) or 'Resume'}_Resume.pdf"


async def build_resume_file(resume: TailoredResume, settings: AutomationSettings,
                            out_dir: Path) -> tuple[Path, int]:
    """Render and save the PDF, plus a JSON copy recording each bullet's source fact."""
    pdf, dropped = await render_pdf(resume, settings)
    path = resume_path(out_dir, resume.job_id, resume.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    path.with_name("sources.json").write_text(resume.model_dump_json(indent=2), encoding="utf-8")
    return path, dropped
