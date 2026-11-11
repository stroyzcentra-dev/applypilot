"""Types for a tailored resume."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BulletDraft(BaseModel):
    fact_id: str = Field(description="The one fact this bullet restates. Each fact at most once.")
    text: str = Field(description="The bullet, reworded to put what this posting cares about first.")


class ResumeDraft(BaseModel):
    """What the model decides. Everything else on the page comes from the profile, in code."""

    summary: str = Field(description="One or two sentences about the candidate, for this role.")
    summary_fact_ids: list[str] = Field(description="Facts the summary rests on.")
    bullets: list[BulletDraft] = Field(
        description="Most relevant first. Only experience, achievement and project facts."
    )
    skills: list[str] = Field(description="Skills to list, most relevant first, from the facts' skills.")


class Bullet(BaseModel):
    fact_id: str
    text: str
    verified: bool  # False: the draft failed review, so the fact's own text is used instead
    issues: list[str] = Field(default_factory=list)


class Role(BaseModel):
    org: str
    title: str | None = None
    dates: str | None = None
    bullets: list[Bullet] = Field(default_factory=list)


class Project(BaseModel):
    bullet: Bullet
    link: str | None = None


class TailoredResume(BaseModel):
    """The finished resume: layout from the profile, wording verified against the facts."""

    job_id: str
    name: str
    contact: list[str]
    summary: str
    summary_verified: bool
    roles: list[Role]
    projects: list[Project]
    education: list[str]
    skills: list[str]
    issues: list[str] = Field(default_factory=list)

    @property
    def all_bullets(self) -> list[Bullet]:
        return [b for r in self.roles for b in r.bullets] + [p.bullet for p in self.projects]
