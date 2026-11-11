"""The Matcher agent: score how well a posting fits the candidate.

The model reads the posting and the fact bank and returns a FitAssessment.
Then plain code checks its work:
  * every cited fact ID must exist in the fact bank,
  * a requirement marked met or partial must cite at least one real fact,
  * stated dealbreakers cap the score.
The model's opinion never goes into the database without passing these checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from applypilot.llm import LLM
from applypilot.models import FitAssessment, JobPosting, Profile

# Long descriptions are rare, but a 60k-char posting shouldn't cost 60k chars.
# The useful part (responsibilities, requirements) is almost always near the top.
MAX_DESCRIPTION_CHARS = 20_000
UNGROUNDED_PENALTY = 5
DEALBREAKER_CAP = 20

SYSTEM_TEMPLATE = """\
You are the Matcher in a job search pipeline. You judge how well one job \
posting fits one candidate, for the candidate's own benefit: an honest "no" \
saves them a wasted application.

The candidate's fact bank is below. It is the only thing you know about them. \
Each fact has an ID in square brackets.

<fact_bank>
{facts}
</fact_bank>

<candidate_constraints>
Work authorization: {work_auth}
Needs visa sponsorship: {sponsorship}
Location: {location}
</candidate_constraints>

How to assess:
1. Pull the posting's real requirements: the must-haves and the stated \
nice-to-haves. Skip boilerplate (benefits, EEO statements, "team player").
2. For each requirement, decide met / partial / missing, and cite the fact IDs \
that support it. If no fact supports it, it is missing, even if the candidate \
probably has the skill. Don't infer experience the facts don't state.
3. List dealbreakers only when the posting states them explicitly and the \
candidate fails them (security clearance, sponsorship not offered, required \
degree they lack, years-of-experience floor clearly above theirs).
4. Score 0-100 for how likely a recruiter is to move this candidate forward. \
85+ means most must-haves are met with evidence. 70-84 means a credible \
application with some gaps. Below 50 means applying is probably wasted effort.

Writing rules for `summary` and `note`:
- Plain, specific sentences. Name the actual technology, domain or number.
- No filler or hype: never write "passionate", "leverage", "synergy", \
"strong fit", "great opportunity", "excited", "aligns perfectly".
- Don't restate the requirement in the note; say what the evidence is or isn't.
"""


@dataclass
class ScoredJob:
    assessment: FitAssessment
    grounding_issues: list[str] = field(default_factory=list)


def build_system(profile: Profile) -> str:
    p = profile.person
    return SYSTEM_TEMPLATE.format(
        facts=profile.facts_as_prompt(),
        work_auth=p.work_authorization or "not stated",
        sponsorship="yes" if p.needs_sponsorship else "no",
        location=p.location,
    )


def build_user(job: JobPosting, today: date | None = None) -> str:
    # The date goes here, not in the system prompt, so the cached prefix stays stable.
    # Without it the model guesses "now" and miscounts years of experience.
    today = today or date.today()
    desc = job.description[:MAX_DESCRIPTION_CHARS]
    salary = ""
    if job.salary_min_usd or job.salary_max_usd:
        salary = f"\nSalary (USD/yr): {job.salary_min_usd or '?'} to {job.salary_max_usd or '?'}"
    return (
        f"<posting>\nCompany: {job.company}\nTitle: {job.title}\n"
        f"Location: {job.location or 'not stated'}{' (remote)' if job.remote else ''}"
        f"{salary}\n\n{desc}\n</posting>\n\n"
        f"Today is {today.isoformat()}; an end date of 'present' means today.\nAssess this posting."
    )


def verify(assessment: FitAssessment, profile: Profile) -> ScoredJob:
    """Deterministic checks on the model's assessment. Returns a corrected copy."""
    a = assessment.model_copy(deep=True)
    known = profile.fact_ids
    issues: list[str] = []

    for req in a.requirements:
        bogus = [fid for fid in req.fact_ids if fid not in known]
        if bogus:
            issues.append(f"'{req.requirement}': cited unknown fact IDs {bogus}")
            req.fact_ids = [fid for fid in req.fact_ids if fid in known]
        if req.status != "missing" and not req.fact_ids:
            issues.append(f"'{req.requirement}': marked {req.status} with no evidence; set to missing")
            req.status = "missing"
            a.score = max(0, a.score - UNGROUNDED_PENALTY)
        if req.status == "missing" and req.fact_ids:
            req.fact_ids = []

    if a.dealbreakers:
        a.score = min(a.score, DEALBREAKER_CAP)
        a.verdict = "no"

    return ScoredJob(assessment=a, grounding_issues=issues)


class Matcher:
    def __init__(self, llm: LLM, profile: Profile):
        self.llm = llm
        self.profile = profile
        self._system = build_system(profile)

    def score(self, job: JobPosting) -> ScoredJob:
        raw = self.llm.structured(
            system=self._system,
            user=build_user(job),
            schema=FitAssessment,
            effort="medium",
        )
        return verify(raw, self.profile)
