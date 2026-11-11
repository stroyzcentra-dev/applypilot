"""The Tailor agent: a resume for one posting, built only from the fact bank.

Division of labour:
  model  picks which facts to feature, orders them, and rewords each into one bullet
  code   everything structural: name, contact, employers, titles, dates, education,
         grouping, and which skills are allowed (only skills the facts list)

Every bullet goes through the same Verifier as form answers (sources, numbers,
lint, then the LLM critic). A bullet that fails is retried once; if it still
fails, the fact's own text is used, so a resume can always be produced and
never says more than the profile.
"""

from __future__ import annotations

import logging
import re

from applypilot.llm import LLM
from applypilot.models import Fact, JobPosting, Profile
from applypilot.tailor.models import Bullet, Project, ResumeDraft, Role, TailoredResume
from applypilot.writer.checks import BANNED_PHRASES, check
from applypilot.writer.models import DraftAnswer, Question, Sentence, Source
from applypilot.writer.writer import Critic

log = logging.getLogger(__name__)

MAX_POSTING_CHARS = 12_000
BULLET_KINDS = ("experience", "achievement", "project")
MAX_BULLET_CHARS = 220

TAILOR_SYSTEM = """\
You are the Tailor in a job application pipeline. You choose and reword resume \
bullets for one posting. You never add anything the facts don't state.

<fact_bank>
{facts}
</fact_bank>

Return:
- bullets: one per fact you feature, most relevant to the posting first. Use only \
experience, achievement and project facts, each at most once. Reword the fact so \
what this posting cares about comes first, but keep every claim and number exactly \
as the fact states it. Never add scope, team size, results, tools or numbers, \
and never add descriptive embellishment the fact doesn't state ("from the ground \
up", "end to end", "production-grade", "robust", "scalable", "complex").
- summary: one or two sentences describing the candidate for this role, built only \
from facts you list in summary_fact_ids. No adjectives about character.
- skills: up to 12 skills, most relevant first, copied from the facts' skills lists.

Bullet style: start with a past-tense verb (present tense for an ongoing role is \
fine), no "I", no ending period, under {max_chars} characters. Never use: {banned}. \
No em dashes.
"""


def _verify_text(text: str, fact_ids: list[str], profile: Profile, job: JobPosting,
                 kind: str = "short_text") -> list[str]:
    q = Question(key="b", label="resume bullet", kind=kind, required=True,
                 max_length=MAX_BULLET_CHARS if kind == "short_text" else 400)
    d = DraftAnswer(key="b", action="write", sentences=[
        Sentence(text=text, sources=[Source(kind="fact", ref=f) for f in fact_ids])])
    issues = [i.message for i in check(d, q, profile, job)]
    if _FIRST_PERSON.search(text):
        issues.append("Resumes don't use first person (I, me, my); start with the verb.")
    return issues


_FIRST_PERSON = re.compile(r"\b(I|I'm|I've|me|my|myself)\b")


class Tailor:
    def __init__(self, llm: LLM, critic: Critic, profile: Profile):
        self.llm = llm
        self.critic = critic
        self.profile = profile
        self._facts = {f.id: f for f in profile.facts}
        self._system = TAILOR_SYSTEM.format(
            facts=profile.facts_as_prompt(),
            max_chars=MAX_BULLET_CHARS,
            banned=", ".join(f'"{p}"' for p in BANNED_PHRASES),
        )

    # ---- model step ---------------------------------------------------------

    def _draft(self, job: JobPosting, feedback: str = "") -> ResumeDraft:
        user = (f"<posting>\nCompany: {job.company}\nTitle: {job.title}\n\n"
                f"{job.description[:MAX_POSTING_CHARS]}\n</posting>\n")
        if feedback:
            user += ("\nYour previous draft failed review. Fix exactly these problems, keeping "
                     f"each bullet to what its fact says:\n{feedback}\n")
        return self.llm.structured(system=self._system, user=user, schema=ResumeDraft, effort="medium")

    # ---- verification -------------------------------------------------------

    def _structural_issues(self, d: ResumeDraft) -> dict[str, list[str]]:
        """Per-bullet problems found by code. Key '' holds draft-level problems."""
        out: dict[str, list[str]] = {"": []}
        seen: set[str] = set()
        for b in d.bullets:
            f = self._facts.get(b.fact_id)
            msgs = out.setdefault(b.fact_id, [])
            if f is None:
                msgs.append(f"{b.fact_id} isn't a fact in the profile.")
            elif f.kind not in BULLET_KINDS:
                msgs.append(f"{b.fact_id} is a {f.kind} fact; it belongs in its own section.")
            if b.fact_id in seen:
                msgs.append(f"{b.fact_id} is used twice.")
            seen.add(b.fact_id)
        for fid in d.summary_fact_ids:
            if fid not in self._facts:
                out[""].append(f"summary cites unknown fact {fid}")
        return out

    def _check_bullets(self, job: JobPosting, d: ResumeDraft) -> dict[str, list[str]]:
        issues = self._structural_issues(d)
        for b in d.bullets:
            if b.fact_id in self._facts and not issues[b.fact_id]:
                issues[b.fact_id] += _verify_text(b.text, [b.fact_id], self.profile, job)
        # The critic reads each surviving bullet next to its fact.
        ok = [b for b in d.bullets if not issues.get(b.fact_id)]
        if ok:
            q = Question(key="resume", label=f"Resume bullets for {job.title}", kind="long_text",
                         required=True)
            draft = DraftAnswer(key="resume", action="write", sentences=[
                Sentence(text=b.text, sources=[Source(kind="fact", ref=b.fact_id)]) for b in ok])
            for i in self.critic.review(job, [(q, draft)]):
                if i.code == "off_question" or i.sentence is None:
                    continue  # bullets aren't answers to a question; only per-claim checks apply
                if i.code == "overstated" and 0 <= i.sentence < len(ok):
                    issues[ok[i.sentence].fact_id].append(i.message)
        return issues

    # ---- assembly -----------------------------------------------------------

    def tailor(self, job: JobPosting) -> TailoredResume:
        draft = self._draft(job)
        issues = self._check_bullets(job, draft)
        failed = {k: v for k, v in issues.items() if k and v}
        if failed:
            feedback = "\n".join(f"- {fid}: {'; '.join(msgs)}" for fid, msgs in failed.items())
            retry = self._draft(job, feedback)
            retry_issues = self._check_bullets(job, retry)
            if sum(map(len, retry_issues.values())) < sum(map(len, issues.values())):
                draft, issues = retry, retry_issues
        return self._assemble(job, draft, issues)

    def _assemble(self, job: JobPosting, d: ResumeDraft, issues: dict[str, list[str]]) -> TailoredResume:
        p, facts = self.profile, self._facts

        bullets: list[Bullet] = []
        for b in d.bullets:
            f = facts.get(b.fact_id)
            if f is None or f.kind not in BULLET_KINDS or any(x.fact_id == f.id for x in bullets):
                continue
            problems = issues.get(f.id, [])
            text = b.text.strip().rstrip(".") if not problems else f.text.rstrip(".")
            bullets.append(Bullet(fact_id=f.id, text=text, verified=not problems, issues=problems))

        # Every job in the profile appears, even if no bullet was chosen for it,
        # so the tailored resume never hides part of the work history.
        roles: dict[str, Role] = {}
        for f in sorted((f for f in p.facts if f.kind == "experience"),
                        key=lambda f: f.start or "", reverse=True):
            roles[f.org or f.id] = Role(org=f.org or "", title=f.role,
                                        dates=_dates(f) or None)
        projects: list[Project] = []
        for b in bullets:
            f = facts[b.fact_id]
            if f.kind == "project":
                projects.append(Project(bullet=b, link=_short_link(str(f.evidence)) if f.evidence else None))
            elif f.org and f.org in roles:
                roles[f.org].bullets.append(b)
            else:
                roles.setdefault(f.org or "Other", Role(org=f.org or "Other")).bullets.append(b)

        summary_ids = [i for i in d.summary_fact_ids if i in facts]
        summary_problems = (_verify_text(d.summary, summary_ids, p, job, "long_text")
                            if summary_ids else ["summary cites no facts"])
        summary = d.summary.strip() if not summary_problems else ""

        allowed = {s.lower(): s for f in p.facts for s in f.skills}
        skills = list(dict.fromkeys(allowed[s.lower()] for s in d.skills if s.lower() in allowed))[:12]

        links = [_short_link(str(u)) for u in p.person.links.values()]
        contact = [x for x in (p.person.location, p.person.email, p.person.phone, *links) if x]
        all_issues = [f"{k}: {m}" for k, v in issues.items() for m in v] + \
                     [f"summary: {m}" for m in summary_problems]
        return TailoredResume(
            job_id=job.id, name=p.person.name, contact=contact, summary=summary,
            summary_verified=not summary_problems, roles=list(roles.values()), projects=projects,
            education=[_education(f) for f in p.facts if f.kind == "education"], skills=skills,
            issues=all_issues,
        )


EN_DASH = chr(0x2013)  # date ranges on a resume read better with an en dash
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month(value: str) -> str:
    """'2024-06' -> 'Jun 2024'; anything else is shown as written."""
    year, _, month = value.partition("-")
    if year.isdigit() and month.isdigit() and 1 <= int(month) <= 12:
        return f"{_MONTHS[int(month) - 1]} {year}"
    return value


def _dates(f: Fact) -> str:
    if not f.start:
        return ""
    end = "Present" if (f.end or "").lower() == "present" else _month(f.end or "")
    return f"{_month(f.start)} {EN_DASH} {end}" if end else _month(f.start)


def _education(f: Fact) -> str:
    text = f.text.rstrip(".")
    return f"{f.org}, {text}" if f.org and f.org.lower() not in text.lower() else text


def _short_link(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/")


def drop_least_relevant(r: TailoredResume) -> bool:
    """Remove the last-ranked bullet, keeping at least one per role. False if nothing to drop."""
    if r.projects:
        r.projects.pop()
        return True
    for role in sorted(r.roles, key=lambda x: len(x.bullets), reverse=True):
        if len(role.bullets) > 1:
            role.bullets.pop()
            return True
    return False

