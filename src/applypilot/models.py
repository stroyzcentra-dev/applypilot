"""Core data models shared by every agent.

Everything that crosses an agent boundary is one of these validated types,
never free text, so a bad output fails loudly at the boundary instead of
quietly corrupting the next step.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

# --------------------------------------------------------------------------
# Fact bank (the user's profile)
# --------------------------------------------------------------------------

FactKind = Literal["experience", "achievement", "project", "skill", "education", "other"]


class Fact(BaseModel):
    """One small, checkable claim about the candidate. The unit of grounding."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: FactKind
    text: str = Field(min_length=10)
    org: str | None = None
    role: str | None = None
    start: str | None = None
    end: str | None = None
    skills: list[str] = Field(default_factory=list)
    evidence: HttpUrl | None = None


class Person(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = None
    location: str = Field(min_length=1)
    links: dict[str, HttpUrl] = Field(default_factory=dict)
    work_authorization: str | None = None
    needs_sponsorship: bool = False
    resume_file: str | None = Field(default=None, description="Path to your resume PDF.")

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def last_name(self) -> str:
        return " ".join(self.name.split()[1:])


class Preferences(BaseModel):
    target_titles: list[str] = Field(min_length=1)
    avoid_title_keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    min_base_salary_usd: int | None = None
    exclude_companies: list[str] = Field(default_factory=list)
    max_posting_age_days: int | None = 30


class StandardAnswers(BaseModel):
    """Your answers to questions that come up on most forms.

    These are facts too: the Writer cites them as `answer:<key>` and may only
    answer a logistics question (start date, relocation, salary) from here.
    Leave one empty and that question is left for you.
    """

    work_location: str | None = Field(None, description="Where you'd work from, e.g. 'Raleigh, NC'.")
    willing_to_relocate: bool | None = None
    relocation_targets: list[str] = Field(default_factory=list)
    open_to_remote: bool | None = None
    earliest_start: str | None = None
    salary_expectation: str | None = None
    how_did_you_hear: str | None = None
    # Voluntary self-identification (gender, race, veteran, disability).
    # "decline" picks the decline-to-answer option when one exists; "ask_me" leaves them to you.
    self_identification: Literal["decline", "ask_me"] = "ask_me"
    extra: dict[str, str] = Field(default_factory=dict, description="Anything else you answer often.")

    def as_sources(self, person: Person) -> dict[str, str]:
        """Every non-empty answer as key -> plain text, including authorization from Person."""
        yes_no = lambda b: "Yes" if b else "No"  # noqa: E731
        out: dict[str, str] = {}
        if person.work_authorization:
            out["work_authorization"] = person.work_authorization
        out["needs_sponsorship"] = yes_no(person.needs_sponsorship)
        for key in ("work_location", "earliest_start", "salary_expectation", "how_did_you_hear"):
            if (v := getattr(self, key)) is not None:
                out[key] = v
        for key in ("willing_to_relocate", "open_to_remote"):
            if (v := getattr(self, key)) is not None:
                out[key] = yes_no(v)
        if self.relocation_targets:
            out["relocation_targets"] = ", ".join(self.relocation_targets)
        if self.self_identification == "decline":
            out["self_identification"] = "Decline to self-identify on voluntary demographic questions."
        out.update(self.extra)
        return out


class Profile(BaseModel):
    person: Person
    preferences: Preferences
    facts: list[Fact] = Field(min_length=1)
    answers: StandardAnswers = Field(default_factory=StandardAnswers)

    @property
    def answer_sources(self) -> dict[str, str]:
        return self.answers.as_sources(self.person)

    @field_validator("facts")
    @classmethod
    def _unique_ids(cls, facts: list[Fact]) -> list[Fact]:
        seen: set[str] = set()
        for f in facts:
            if f.id in seen:
                raise ValueError(f"duplicate fact id: {f.id}")
            seen.add(f.id)
        return facts

    @property
    def fact_ids(self) -> set[str]:
        return {f.id for f in self.facts}

    def facts_as_prompt(self) -> str:
        """Render the fact bank in a stable, citeable form for prompts."""
        lines = []
        for f in self.facts:
            where = " / ".join(x for x in (f.role, f.org) if x)
            when = f"{f.start} to {f.end}" if f.start else ""
            meta = "; ".join(x for x in (where, when) if x)
            skills = f" [skills: {', '.join(f.skills)}]" if f.skills else ""
            lines.append(f"[{f.id}] ({f.kind}{'; ' + meta if meta else ''}) {f.text}{skills}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

SourceName = Literal["greenhouse", "lever", "ashby"]


class JobPosting(BaseModel):
    """A posting normalized from any source."""

    source: SourceName
    external_id: str
    company: str
    title: str
    url: str
    apply_url: str | None = None
    location: str = ""
    remote: bool = False
    description: str = ""
    department: str | None = None
    posted_at: datetime | None = None
    salary_min_usd: int | None = None
    salary_max_usd: int | None = None

    @property
    def id(self) -> str:
        return f"{self.source}:{self.company}:{self.external_id}"


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    FILTERED_OUT = "filtered_out"
    SCORED = "scored"
    BELOW_THRESHOLD = "below_threshold"
    DRAFTING = "drafting"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FAILED = "failed"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"


# --------------------------------------------------------------------------
# Matcher output
# --------------------------------------------------------------------------


class RequirementMatch(BaseModel):
    """How one requirement from the posting lines up with the candidate."""

    requirement: str = Field(description="A requirement quoted or closely paraphrased from the posting.")
    status: Literal["met", "partial", "missing"]
    fact_ids: list[str] = Field(
        description="IDs of facts that support this. Must be empty when status is 'missing'."
    )
    note: str = Field(description="One plain sentence. No adjectives the facts don't support.")


class FitAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: Literal["strong", "possible", "weak", "no"]
    summary: str = Field(description="Two sentences at most, specific to this posting.")
    requirements: list[RequirementMatch]
    dealbreakers: list[str] = Field(
        default_factory=list,
        description="Hard blockers stated in the posting, e.g. clearance or sponsorship.",
    )
