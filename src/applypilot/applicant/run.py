"""One application run: open the form, fill what's known, draft and verify the rest, submit or ask.

Each run has its own browser context (see LiveBrowser), so no tab, cookie or
half-filled form carries over from one application to the next.

Submission rules:
  1. A site that prohibits automation is handed to the user before the agent loads it.
  2. The agent submits on its own only when ALL of these hold, and says which failed
     when they don't:
       - applicant.mode is "auto"
       - the fit score is at least applicant.auto_submit_min_score
       - the site's policy allows agent submission
       - every required field has a value
       - every drafted answer passed the Verifier
       - today's submissions are under applicant.daily_submit_cap
  3. Otherwise it stops at the submit button. "Submit for me" re-checks the policy
     and required fields right before clicking, and is refused in "draft" mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from applypilot.applicant.forms import (
    FormField,
    fill_standard_fields,
    find_submit,
    has_resume_upload,
    required_still_empty,
    scan,
)
from applypilot.config import Settings
from applypilot.live.browser import AgentPage, HandedOff, LiveBrowser
from applypilot.live.control import Mode, RunCancelled, RunControl
from applypilot.live.events import EventBus
from applypilot.models import ApplicationStatus as S
from applypilot.models import JobPosting, Profile
from applypilot.store import JobRow, Store
from applypilot.tailor import Tailor, build_resume_file
from applypilot.writer import Critic, Question, VerifiedAnswer, Writer, draft_and_verify

log = logging.getLogger(__name__)

# Statuses an application run may start from. Re-running a job that's waiting for
# approval (say, after a browser restart) moves it back to drafting first.
STARTABLE = frozenset({S.DISCOVERED, S.SCORED, S.BELOW_THRESHOLD, S.FAILED, S.DRAFTING, S.AWAITING_APPROVAL})


@dataclass
class Run:
    job_id: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    bus: EventBus = field(default_factory=EventBus)
    control: RunControl = field(default_factory=RunControl)
    outcome: str | None = None
    warning: str | None = None
    needs_input: list[str] = field(default_factory=list)


@dataclass
class Agents:
    """The model-backed agents a run can use. None means drafting is off (no API key)."""

    writer: Writer | None = None
    critic: Critic | None = None
    tailor: Tailor | None = None


async def run_application(
    run: Run, *, store: Store, settings: Settings, profile: Profile, agents: Agents | None = None
) -> None:
    agents = agents or Agents()
    bus, control = run.bus, run.control
    row = store.get(run.job_id)
    if row is None:
        bus.emit("error", "Job not found", detail=run.job_id)
        return _finish(run, "error")

    job = row.posting
    url = job.apply_url or job.url
    bus.emit("status", f"Applying: {job.title} at {job.company}", detail=url, url=url)
    if row.status in STARTABLE and row.status != S.DRAFTING:
        store.transition(job.id, S.DRAFTING, actor="applicant", note="opened in live browser")

    browser = LiveBrowser(settings.automation)
    ap: AgentPage | None = None
    try:
        await browser.start()
        if browser.closed_leftovers:
            bus.emit(
                "info",
                f"Closed {browser.closed_leftovers} tabs left from earlier",
                detail="Each application starts in a clean browser: no tabs, cookies or "
                "form drafts from other applications.",
            )
        ap = AgentPage(
            await browser.new_page(), bus=bus, control=control, settings=settings.automation, run_id=run.id
        )

        await ap.goto(url, why="Open the employer's application form.")
        if ap.decision and ap.decision.warning:
            run.warning = ap.decision.warning
        await _reveal_form(ap)

        resume = await _tailor_resume(ap, job, agents, settings)
        result = await fill_standard_fields(ap, profile.person, tailored_resume=resume)
        await ap.snapshot(
            f"Filled {len(result.filled)} fields from your profile",
            detail=", ".join(result.filled) or "No standard fields found.",
        )

        verified = await _draft_open_questions(ap, job, agents)

        empty = await required_still_empty(ap)
        run.needs_input = empty
        if empty:
            bus.emit("needs_input", f"{len(empty)} required fields need your answer", detail="\n".join(empty))

        store.transition(
            job.id,
            S.AWAITING_APPROVAL,
            actor="applicant",
            note=f"filled {len(result.filled)}, drafted "
            f"{sum(v.passed for v in verified)}, needs you: {len(empty)}",
        )

        blockers = _autosubmit_blockers(settings, row, ap, empty, verified, store)
        if not blockers:
            bus.emit(
                "status",
                "Submitting automatically",
                detail="Every auto-submit condition holds: auto mode, score, site policy, "
                "all required fields filled, every draft verified, under the daily cap.",
            )
            if await _submit(run, ap, store, job.id, actor="applicant", note="auto-submitted"):
                return
        else:
            bus.emit(
                "status",
                "Ready for your decision",
                detail="Not submitting on its own because:\n- " + "\n- ".join(blockers),
            )
        await _decide_and_submit(run, ap, store, job.id, settings)

    except HandedOff as h:
        run.warning = h.decision.warning
        with contextlib.suppress(Exception):  # don't leave the agent's blank tab behind
            if ap and ap.page.url == "about:blank":
                await ap.page.close()
        if store.get(job.id).status == S.DRAFTING:
            store.transition(
                job.id,
                S.AWAITING_APPROVAL,
                actor="policy",
                note=f"handed to you: {h.decision.host} prohibits automation",
            )
        bus.emit(
            "status",
            "The browser is yours",
            detail="Press “Open page” to load the posting in the live view, apply there, then "
            "press “I submitted it” so the log stays accurate.",
        )
        await _user_only(run, store, job.id, browser, url)
    except RunCancelled:
        bus.emit("status", "Run cancelled")
        _finish(run, "cancelled")
    except Exception as exc:
        log.exception("run %s failed", run.id)
        bus.emit("error", "The run hit an error", detail=f"{type(exc).__name__}: {exc}")
        _finish(run, "error")
    finally:
        await browser.stop()


async def _reveal_form(ap: AgentPage) -> None:
    """Some boards show the description first, with an Apply button that reveals the form."""
    if await ap.page.locator("input[type=email], input[name*=email i]").count():
        return
    apply_btn = ap.page.get_by_role("button", name="Apply").or_(
        ap.page.get_by_role("link", name="Apply for this job")
    )
    if await apply_btn.count():
        await ap.click(apply_btn.first, label="Apply", why="Reveal the application form.")


# ---- tailored resume ----------------------------------------------------------


async def _tailor_resume(ap: AgentPage, job: JobPosting, agents: Agents, settings: Settings) -> Path | None:
    """Tailor and render a resume, but only when the form actually asks for one."""
    if agents.tailor is None or not has_resume_upload(await scan(ap)):
        return None
    ap.bus.emit("info", "Tailoring your resume for this posting",
                detail="Choosing and rewording facts; names, employers, titles and dates come "
                       "straight from your profile.")
    await ap.control.checkpoint()
    try:
        resume = await asyncio.to_thread(agents.tailor.tailor, job)
        path, dropped = await build_resume_file(resume, settings.automation,
                                                settings.automation.resumes_dir)
    except Exception as exc:  # fall back to the profile's resume file, if any
        log.exception("tailoring failed")
        ap.bus.emit("warning", "Couldn't tailor a resume", detail=f"{type(exc).__name__}: {exc}")
        return None

    sentences = [{"text": resume.summary, "sources": ["summary"]}] if resume.summary else []
    sentences += [{"text": b.text + ("" if b.verified else " (your fact's own wording)"),
                   "sources": [f"fact:{b.fact_id}"]} for b in resume.all_bullets]
    unverified = [b.fact_id for b in resume.all_bullets if not b.verified]
    detail = (f"{len(resume.all_bullets)} bullets"
              + (f", {dropped} dropped to fit one page" if dropped else "")
              + (f"; {len(unverified)} used the fact's wording after the reworded bullet failed review"
                 if unverified else ""))
    ap.bus.emit("draft", "Tailored resume ready", detail=detail, sentences=sentences,
                issues=resume.issues or None,
                file=path.relative_to(settings.automation.resumes_dir).as_posix())
    return path


# ---- drafting ---------------------------------------------------------------


def _open_questions(fields: list[FormField]) -> list[FormField]:
    """Fields worth drafting: required ones still empty, plus optional free-text questions."""
    out = []
    for f in fields:
        if f.filled or not f.clean_label or f.kind in ("file", "typeahead"):
            continue
        if f.required or f.kind == "long_text":
            out.append(f)
    return out


async def _draft_open_questions(ap: AgentPage, job: JobPosting, agents: Agents) -> list[VerifiedAnswer]:
    fields = {f.key: f for f in _open_questions(await scan(ap))}
    if not fields:
        return []
    if agents.writer is None or agents.critic is None:
        ap.bus.emit(
            "info",
            "Drafting is off",
            detail="Set ANTHROPIC_API_KEY to have the Writer draft answers to the open questions.",
        )
        return []

    questions = [
        Question(
            key=f.key,
            label=f.clean_label,
            kind=f.kind,
            required=f.required,
            options=[c["label"] for c in f.choices],
            max_length=f.max_length,
        )
        for f in fields.values()
    ]
    ap.bus.emit("info", f"Drafting {len(questions)} answers", detail="\n".join(q.label for q in questions))
    await ap.control.checkpoint()
    verified = await asyncio.to_thread(draft_and_verify, agents.writer, agents.critic, job, questions)

    for v in verified:
        f, d = fields[v.question.key], v.draft
        label = v.question.label
        if d.action == "skip":
            ap.bus.emit("needs_input", f"Left for you: “{label}”", detail=d.skip_reason)
            continue
        if not v.passed:
            ap.bus.emit(
                "draft_failed",
                f"Draft rejected: “{label}”",
                detail=d.text or ", ".join(d.choices),
                issues=[i.message for i in v.issues],
                sentences=_sentences(d),
            )
            continue
        await _apply(ap, f, v)
    return verified


def _sentences(d) -> list[dict]:
    if d.action == "choose":
        return [{"text": ", ".join(d.choices), "sources": [f"{s.kind}:{s.ref}" for s in d.choice_sources]}]
    return [{"text": s.text, "sources": [f"{x.kind}:{x.ref}" for x in s.sources]} for s in d.sentences]


async def _apply(ap: AgentPage, f: FormField, v: VerifiedAnswer) -> None:
    d, label = v.draft, v.question.label
    cited = sorted({src for s in _sentences(d) for src in s["sources"]})
    why = "Verified draft. Sources: " + ", ".join(cited)
    loc = ap.page.locator(f'[data-ap-field="{f.key}"]')
    if d.action == "write":
        await ap.fill(loc, d.text, label=label, why=why)
    elif f.tag == "select":
        await ap.select(loc, d.choices[0], label=label, why=why)
    else:  # checkbox / radio group
        by_label = {c["label"]: c["key"] for c in f.choices}
        for choice in d.choices:
            opt = ap.page.locator(f'[data-ap-field="{by_label[choice]}"]')
            await ap.check(opt, option=choice, label=label, why=why)
    ap.bus.emit(
        "draft", f"Answered “{label}”", detail=d.text or ", ".join(d.choices), sentences=_sentences(d)
    )


# ---- submission ---------------------------------------------------------------


def _autosubmit_blockers(
    settings: Settings,
    row: JobRow,
    ap: AgentPage,
    empty: list[str],
    verified: list[VerifiedAnswer],
    store: Store,
) -> list[str]:
    a = settings.applicant
    out = []
    if a.mode != "auto":
        out.append(f"submission mode is '{a.mode}'")
    if row.score is None or row.score < a.auto_submit_min_score:
        out.append(
            f"fit score {row.score if row.score is not None else 'unscored'} "
            f"is below {a.auto_submit_min_score}"
        )
    if ap.decision and not ap.decision.may_autosubmit:
        out.append(f"{ap.decision.host}'s policy requires a person to submit")
    if empty:
        out.append(f"{len(empty)} required fields are empty")
    failed = [v.question.label for v in verified if not v.passed]
    if failed:
        out.append(f"{len(failed)} drafts didn't pass the Verifier")
    if (n := store.submitted_today()) >= a.daily_submit_cap:
        out.append(f"daily cap reached ({n} of {a.daily_submit_cap} today)")
    return out


async def _submit(run: Run, ap: AgentPage, store: Store, job_id: str, *, actor: str, note: str) -> bool:
    """Re-check, click submit, record it. Returns False (with a timeline note) if it can't."""
    bus = run.bus
    ap.check_policy()
    if ap.decision and ap.decision.policy.level == "prohibited":
        bus.emit("warning", "Not submitting", detail="This site prohibits automation; submit it yourself.")
        return False
    empty = await required_still_empty(ap)
    if empty:
        bus.emit("needs_input", "Can't submit yet: required fields are empty", detail="\n".join(empty))
        return False
    button = await find_submit(ap)
    if button is None:
        bus.emit("warning", "Couldn't find the submit button", detail="Submit it yourself in the live view.")
        return False

    store.transition(job_id, S.SUBMITTING, actor=actor, note=note)
    await ap.click(button, label="Submit", why=note[:1].upper() + note[1:] + ".")
    await ap.page.wait_for_timeout(3000)
    await ap.snapshot("After submitting", detail="Check the page for a confirmation message.")
    store.transition(job_id, S.SUBMITTED, actor=actor, note=note)
    _finish(run, "submitted")
    return True


async def _decide_and_submit(run: Run, ap: AgentPage, store: Store, job_id: str, settings: Settings) -> None:
    bus, control = run.bus, run.control
    while True:
        choice = await control.await_decision()
        if choice == "cancel":
            raise RunCancelled
        if choice == "user_submitted":
            store.transition(job_id, S.SUBMITTED, actor="user", note="submitted by you in the live browser")
            bus.emit("status", "Marked as submitted by you")
            return _finish(run, "submitted")
        if choice != "agent_submit":
            continue
        if settings.applicant.mode == "draft":
            bus.emit("warning", "Not submitting", detail="Submission mode is 'draft'. Submit it yourself.")
            continue
        control.mode = Mode.AGENT
        if await _submit(run, ap, store, job_id, actor="applicant", note="submitted after your approval"):
            return


async def _user_only(run: Run, store: Store, job_id: str, browser: LiveBrowser, url: str) -> None:
    """Handoff: the agent stays off the site. The user drives; we only record the outcome."""
    while True:
        choice = await run.control.await_decision()
        if choice == "open_page":
            # Only on the user's request, the same as them clicking the link. No interaction.
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="commit")
                run.bus.emit("info", "Opened the posting for you", detail=url, url=url)
            except Exception as exc:
                run.bus.emit("error", "Couldn't open the page", detail=str(exc))
            continue
        if choice == "user_submitted":
            store.transition(job_id, S.SUBMITTED, actor="user", note="submitted by you (handoff)")
            run.bus.emit("status", "Marked as submitted by you")
            return _finish(run, "submitted")
        if choice == "cancel":
            run.bus.emit("status", "Run closed")
            return _finish(run, "cancelled")
        run.bus.emit(
            "warning",
            "The agent can't submit here",
            detail="This site prohibits automation. Submit it yourself, then press “I submitted it”.",
        )


def _finish(run: Run, outcome: str) -> None:
    run.outcome = outcome
    run.control.finish()
    run.bus.close()
