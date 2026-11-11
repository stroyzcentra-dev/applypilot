"""End-to-end application runs in a real browser, with scripted agents.

The form is a self-contained data: URL, so no employer site is ever touched.
Needs the desktop container (`applypilot desktop up`); skipped otherwise.
"""

import asyncio
from urllib.parse import quote

import pytest
from playwright.async_api import async_playwright

import applypilot.live.browser as browser_mod
from applypilot.applicant.run import Agents, Run, run_application
from applypilot.config import AutomationSettings
from applypilot.models import ApplicationStatus as S
from applypilot.models import FitAssessment, JobPosting
from applypilot.policy import Action, Decision, Level, SitePolicy
from applypilot.writer.models import DraftAnswer, Sentence, Source

CDP = "http://localhost:9223"

FORM = """<!doctype html><title>Acme application</title>
<form id=f onsubmit="event.preventDefault(); document.body.innerHTML='<h1>Application received</h1>'">
  <label for=fn>First Name *</label><input id=fn required>
  <label for=ln>Last Name *</label><input id=ln required>
  <label for=em>Email *</label><input id=em type=email required>
  <label for=why>Why do you want to work at Acme? *</label><textarea id=why required></textarea>
  <label for=relo>Are you willing to relocate? *</label>
  <select id=relo required><option value=""></option><option>Yes</option><option>No</option></select>
  <label><input type=checkbox id=sms> I consent to SMS messages</label>
  <button type=submit>Submit Application</button>
</form>"""
URL = "data:text/html," + quote(FORM)


class FakeWriter:
    """Answers by question label, the way the real Writer would after review."""

    def __init__(self, profile):
        self.profile = profile

    def draft(self, job, questions, feedback=None, previous=None):
        out = []
        for q in questions:
            if q.label.startswith("Why"):
                out.append(DraftAnswer(key=q.key, action="write", sentences=[Sentence(
                    text="I built a FastAPI service that prices freight quotes.",
                    sources=[Source(kind="fact", ref="exp-acme-api")])]))
            elif "relocate" in q.label:
                out.append(DraftAnswer(key=q.key, action="choose", choices=["No"],
                                       choice_sources=[Source(kind="answer", ref="willing_to_relocate")]))
            else:
                out.append(DraftAnswer(key=q.key, action="skip", skip_reason="consent is yours to give"))
        return out


class PassingCritic:
    def review(self, job, items):
        return []


@pytest.fixture
async def desktop():
    async with async_playwright() as pw:
        try:
            b = await pw.chromium.connect_over_cdp(CDP, timeout=3000)
        except Exception:
            pytest.skip("desktop container isn't running")
        yield b


@pytest.fixture
def allow_test_page(monkeypatch):
    """Treat the data: URL as an allowed ATS form so the auto-submit path can run."""
    allowed = Decision("test-form", SitePolicy("test-form", Level.ALLOWED, "test"), Action.AUTOMATE, None)
    monkeypatch.setattr(browser_mod, "decide", lambda url, accept=None: allowed)


def scored_job(store, score=90) -> JobPosting:
    job = JobPosting(source="greenhouse", external_id="e2e", company="acme", title="Software Engineer",
                     url=URL, apply_url=URL, location="Remote", remote=True, description="APIs.")
    store.add_if_new(job)
    store.transition(job.id, S.SCORED, actor="test", assessment=FitAssessment(
        score=score, verdict="strong", summary="test", requirements=[]))
    return job


def run_settings(settings, tmp_path, mode="auto"):
    s = settings.model_copy(deep=True)
    s.applicant.mode = mode
    s.automation = AutomationSettings(screenshots_dir=tmp_path)
    return s


async def test_auto_submits_when_every_condition_holds(desktop, allow_test_page, store, settings,
                                                       profile, tmp_path):
    # A tab from "an earlier application" that must not survive into this one.
    leftover = await desktop.contexts[0].new_page()
    await leftover.goto("data:text/html,<h1>previous application</h1>")

    job = scored_job(store)
    run = Run(job_id=job.id)
    await asyncio.wait_for(run_application(
        run, store=store, settings=run_settings(settings, tmp_path), profile=profile,
        agents=Agents(writer=FakeWriter(profile), critic=PassingCritic())), timeout=60)

    assert run.outcome == "submitted"
    assert store.get(job.id).status == S.SUBMITTED
    titles = [s.title for s in run.bus.history]
    assert any("tabs left from earlier" in t for t in titles)
    assert "Answered “Why do you want to work at Acme?”" in titles
    assert "Answered “Are you willing to relocate?”" in titles
    assert "Submitting automatically" in titles
    draft = next(s for s in run.bus.history if s.kind == "draft")
    assert draft.sentences[0]["sources"] == ["fact:exp-acme-api"]

    # Isolation: neither the leftover tab nor this application's tabs remain.
    urls = [p.url for c in desktop.contexts for p in c.pages]
    assert not any("previous application" in u or "Acme" in u for u in urls)


async def test_approve_mode_stops_and_says_why(desktop, allow_test_page, store, settings,
                                               profile, tmp_path):
    job = scored_job(store, score=60)
    run = Run(job_id=job.id)
    task = asyncio.create_task(run_application(
        run, store=store, settings=run_settings(settings, tmp_path, mode="approve"), profile=profile,
        agents=Agents(writer=FakeWriter(profile), critic=PassingCritic())))

    for _ in range(100):
        if run.control.mode == "waiting_approval":
            break
        await asyncio.sleep(0.2)
    status = next(s for s in reversed(run.bus.history) if s.title == "Ready for your decision")
    assert "mode is 'approve'" in status.detail and "fit score 60 is below 85" in status.detail
    assert store.get(job.id).status == S.AWAITING_APPROVAL

    run.control.cancel()
    await asyncio.wait_for(task, 10)
    assert run.outcome == "cancelled"
