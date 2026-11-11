"""Helpers behind the dashboard's Profile page.

  save_profile    validate, back up the old file, write atomically
  extract_text    plain text from a PDF, Word or text resume
  ResumeImporter  Claude turns resume text into proposed facts; code then checks
                  each one against the resume itself before the user sees it
  FactRefiner     Claude suggests sharper wording and asks for missing specifics;
                  code rejects any suggestion that adds a number the fact lacks

Nothing here writes to the profile on its own. Imports and refinements are
proposals the user accepts or ignores in the UI, then saves.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from applypilot.config import write_yaml
from applypilot.llm import LLM
from applypilot.models import Fact, Profile

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_RESUME_CHARS = 40_000

HEADER = """\
# ApplyPilot fact bank: the only source of truth about you.
# Edited from the dashboard's Profile page. Every claim the agents write must
# cite one of these facts. The previous version is kept in profile.yaml.bak.
"""

# ---- saving -----------------------------------------------------------------


def save_profile(path: Path, profile: Profile) -> None:
    """Write the profile, keeping the previous file as .bak. Atomic: never half-written."""
    write_yaml(path, profile.model_dump(mode="json", exclude_none=True), HEADER)


# ---- resume text --------------------------------------------------------------


class UnsupportedFile(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnsupportedFile("The file is over 5 MB.")
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif name.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise UnsupportedFile("That doesn't look like a Word (.docx) file.") from exc
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        text = (
            text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
        )
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")
    else:
        raise UnsupportedFile("Upload a PDF, Word (.docx) or text file.")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 40:
        raise UnsupportedFile(
            "Couldn't read any text from that file. If it's a scanned PDF, export it as text first."
        )
    return text[:MAX_RESUME_CHARS]


# ---- grounding helpers ----------------------------------------------------------

_ALNUM = re.compile(r"[^a-z0-9]+")
_NUMBER = re.compile(r"\d[\d,.]*")


def _squash(s: str) -> str:
    """Lowercase, letters and digits only. PDF extraction breaks lines and hyphens
    unpredictably; this makes 'quote appears in the resume' robust to that."""
    return _ALNUM.sub("", s.lower())


def numbers_in(s: str) -> set[str]:
    return {re.sub(r"[^\d.]", "", m).rstrip(".") for m in _NUMBER.findall(s)} - {""}


def new_fact_id(kind: str, label: str, taken: set[str]) -> str:
    prefix = {
        "experience": "exp",
        "achievement": "ach",
        "project": "proj",
        "education": "edu",
        "skill": "skill",
    }.get(kind, "fact")
    words = [w for w in re.findall(r"[a-z0-9]+", label.lower()) if len(w) > 2][:3]
    base = "-".join([prefix, *words]) or prefix
    fid, n = base, 2
    while fid in taken:
        fid, n = f"{base}-{n}", n + 1
    taken.add(fid)
    return fid


# ---- import -------------------------------------------------------------------------

FactKindLit = Literal["experience", "achievement", "project", "skill", "education", "other"]


class ExtractedFact(BaseModel):
    kind: FactKindLit
    text: str = Field(description="One claim, in the resume's own terms. Keep its numbers exactly.")
    source_quote: str = Field(
        description="The exact resume text this fact comes from, copied verbatim (one line or bullet)."
    )
    org: str | None = None
    role: str | None = None
    start: str | None = Field(None, description="YYYY-MM or YYYY, only if the resume states it.")
    end: str | None = Field(None, description="YYYY-MM, YYYY or 'present', only if stated.")
    skills: list[str] = Field(default_factory=list, description="Tools/skills this line names.")


class ExtractedContact(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: dict[str, str] = Field(default_factory=dict, description="e.g. {'github': 'https://...'}")


class ExtractedProfile(BaseModel):
    contact: ExtractedContact
    facts: list[ExtractedFact]
    suggested_titles: list[str] = Field(
        default_factory=list, description="Job titles this person is a credible candidate for."
    )


class ProposedFact(BaseModel):
    fact: Fact
    source_quote: str
    verified: bool
    problems: list[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    contact: ExtractedContact
    facts: list[ProposedFact]
    suggested_titles: list[str]


IMPORT_SYSTEM = """\
You turn a resume into a fact bank for a job application agent. Each fact is \
one small, checkable claim the agent may later repeat to employers, so accuracy \
matters more than coverage.

Rules:
- One claim per fact. Split a bullet with two achievements into two facts.
- Use only what the resume says. Never add numbers, tools, scope or results. \
Keep every number exactly as written.
- source_quote: copy the resume line the fact comes from, verbatim.
- kind: experience for each job (text = what the role was, org/role/dates set); \
achievement for a specific result in a job (org set); project; education \
(org = school); skill: ONE fact for the whole skills section, with every skill \
in `skills`. Never write facts about the resume itself ("lists X among skills").
- Dates only when the resume states them, as YYYY-MM or YYYY; "present" if current.
- Contact: only what's written on the resume.
- suggested_titles: 3 to 6 job titles that match the experience level shown.
"""


class ResumeImporter:
    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, resume_text: str, existing_ids: set[str]) -> ImportResult:
        extracted = self.llm.structured(
            system=IMPORT_SYSTEM,
            user=f"<resume>\n{resume_text}\n</resume>\n\nExtract the contact details and facts.",
            schema=ExtractedProfile,
            effort="medium",
        )
        return verify_import(extracted, resume_text, existing_ids)


_META = re.compile(r"\b(lists?|listed|mentions?|includes?)\b.*\b(skills?|resume)\b", re.IGNORECASE)


def _tidy(facts: list[ExtractedFact]) -> list[ExtractedFact]:
    """Merge every skills-section fact into one, drop facts about the resume itself,
    and drop exact duplicates. Models tend to emit one fact per skill."""
    skills: list[str] = []
    quotes: list[str] = []
    out: list[ExtractedFact] = []
    seen: set[str] = set()
    for f in facts:
        if f.kind == "skill":
            for s in f.skills or [f.text]:
                if s.strip() and s.strip().lower() not in {x.lower() for x in skills}:
                    skills.append(s.strip())
            if f.source_quote not in quotes:
                quotes.append(f.source_quote)
            continue
        if f.kind == "other" and _META.search(f.text):
            continue
        key = _squash(f.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    if skills:
        out.append(
            ExtractedFact(
                kind="skill",
                text="Skills: " + ", ".join(skills) + ".",
                source_quote=max(quotes, key=len),
                skills=skills,
            )
        )
    return out


def verify_import(extracted: ExtractedProfile, resume_text: str, existing_ids: set[str]) -> ImportResult:
    """Check every proposed fact against the resume text, in code."""
    resume = _squash(resume_text)
    resume_numbers = numbers_in(resume_text)
    taken = set(existing_ids)
    out: list[ProposedFact] = []
    for x in _tidy(extracted.facts):
        problems = []
        quote = _squash(x.source_quote)
        if len(quote) < 8 or quote not in resume:
            problems.append("Its quoted line isn't in the resume.")
        extra = numbers_in(x.text) - numbers_in(x.source_quote)
        if extra:
            problems.append(f"Numbers not in the quoted line: {', '.join(sorted(extra))}.")
        if x.org and _squash(x.org) not in resume:
            problems.append(f"“{x.org}” doesn't appear in the resume.")
        for label, val in (("start", x.start), ("end", x.end)):
            year = (val or "")[:4]
            if year.isdigit() and year not in resume_numbers:
                problems.append(f"The {label} year {year} doesn't appear in the resume.")
        fact = Fact(
            # Jobs and schools are named for the org; claims for what they say.
            id=new_fact_id(
                x.kind, (x.org if x.kind in ("experience", "education") else None) or x.text, taken
            ),
            kind=x.kind,
            text=x.text if len(x.text) >= 10 else x.source_quote,
            org=x.org,
            role=x.role,
            start=x.start,
            end=x.end,
            skills=x.skills,
        )
        out.append(
            ProposedFact(fact=fact, source_quote=x.source_quote, verified=not problems, problems=problems)
        )

    c = extracted.contact
    contact = ExtractedContact(
        name=c.name if c.name and _squash(c.name) in resume else None,
        email=c.email if c.email and _squash(c.email) in resume else None,
        phone=c.phone if c.phone and _squash(c.phone) in resume else None,
        location=c.location if c.location and _squash(c.location) in resume else None,
        # Resumes usually write "github.com/you"; the profile stores full URLs.
        links={k.lower(): v if "://" in v else f"https://{v}"
               for k, v in c.links.items() if _squash(v.split("//")[-1]) in resume},
    )
    return ImportResult(contact=contact, facts=out, suggested_titles=extracted.suggested_titles[:6])


# ---- refine -----------------------------------------------------------------------------


class SuggestedFact(BaseModel):
    text: str
    kind: FactKindLit
    skills: list[str] = Field(default_factory=list)


class Refinement(BaseModel):
    verdict: Literal["good", "improve"]
    why: str = Field(description="One sentence: what makes the fact strong, or what's missing.")
    suggestions: list[SuggestedFact] = Field(
        default_factory=list,
        description="Rewordings or a split into separate claims. Never add numbers, tools or scope.",
    )
    questions: list[str] = Field(
        default_factory=list,
        description="Questions whose answers would make the fact specific, e.g. 'How many users?'",
    )


class RefineResult(BaseModel):
    refinement: Refinement
    rejected: list[str] = Field(default_factory=list)


REFINE_SYSTEM = """\
You help a job seeker write their fact bank. Every fact must be one specific, \
checkable claim, because an agent will repeat it to employers.

Given one fact, decide if it's already good. If not:
- suggestions: reword it to be clearer, or split it into separate single-claim \
facts. Use only what the fact says. Never add numbers, tools, scope or results.
- questions: ask for the specifics that would make it stronger (scale, numbers, \
tools, outcome, your exact part in it). The person answers these; you never guess.
Keep it short: at most 3 suggestions and 3 questions.
"""


class FactRefiner:
    def __init__(self, llm: LLM):
        self.llm = llm

    def run(self, fact: Fact) -> RefineResult:
        r = self.llm.structured(
            system=REFINE_SYSTEM,
            user=f"<fact kind={fact.kind!r} org={fact.org!r} role={fact.role!r}>\n{fact.text}\n"
            f"skills: {', '.join(fact.skills) or 'none'}\n</fact>",
            schema=Refinement,
            effort="low",
        )
        return verify_refinement(r, fact)


def verify_refinement(r: Refinement, fact: Fact) -> RefineResult:
    allowed = numbers_in(fact.text)
    kept, rejected = [], []
    for s in r.suggestions:
        extra = numbers_in(s.text) - allowed
        if extra:
            rejected.append(f"“{s.text}” adds numbers the fact doesn't have ({', '.join(sorted(extra))}).")
        else:
            kept.append(s)
    return RefineResult(refinement=r.model_copy(update={"suggestions": kept}), rejected=rejected)
