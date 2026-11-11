"""Deterministic checks on drafted answers. Plain code, no model calls.

These run on every draft before the LLM critic sees it, and their verdict can't
be argued with: a sentence citing a fact that doesn't exist, quoting the posting
inaccurately, or containing a number none of its sources contain, fails.
"""

from __future__ import annotations

import re

from applypilot.models import JobPosting, Profile
from applypilot.writer.models import DraftAnswer, Issue, Question

# Phrases that mark text as generic. Matched as whole words, case-insensitively.
BANNED_PHRASES = (
    "passionate", "passion for", "thrilled", "excited to", "excited about", "leverage",
    "leveraging", "synergy", "synergies", "cutting-edge", "cutting edge", "fast-paced",
    "dynamic", "team player", "go-getter", "hit the ground running", "perfect fit",
    "ideal candidate", "i am confident", "i'm confident", "resonate", "resonates",
    "align with", "aligns with", "aligned with", "testament", "delve", "tapestry",
    "spearhead", "spearheaded", "world-class", "best-in-class", "game-changer",
    "game changer", "deeply", "truly", "incredibly", "unique opportunity",
    "make an impact", "drive impact", "wealth of experience", "proven track record",
    "results-driven", "detail-oriented", "self-starter", "think outside the box",
    "i believe i would", "look no further",
)
_BANNED = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in sorted(BANNED_PHRASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\$?\d[\d,.]*\s?(?:%|k|m|ms|x)?", re.IGNORECASE)
_WS = re.compile(r"\s+")
# Curly quotes and apostrophes compare equal to straight ones when matching posting quotes.
_QUOTES = str.maketrans({chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'})

DEFAULT_MAX = {"short_text": 250, "long_text": 1500}
MIN_QUOTE_CHARS = 12


def _norm(s: str) -> str:
    s = s.lower().translate(_QUOTES)
    return _WS.sub(" ", s).strip()


def _numbers(s: str) -> set[str]:
    out = set()
    for m in _NUMBER.findall(s):
        n = re.sub(r"[^\d.]", "", m).rstrip(".")
        if n:
            out.add(n)
    return out


def source_text(kind: str, ref: str, profile: Profile, job: JobPosting) -> str | None:
    """The text a source points at, or None if it doesn't resolve."""
    if kind == "fact":
        fact = next((f for f in profile.facts if f.id == ref), None)
        if fact is None:
            return None
        parts = [fact.text, fact.org, fact.role, fact.start, fact.end]
        if fact.skills:
            parts.append("skills: " + ", ".join(fact.skills))
        return " ".join(x for x in parts if x)
    if kind == "answer":
        return profile.answer_sources.get(ref)
    if kind == "posting":
        q = _norm(ref.strip('"'))
        return ref if len(q) >= MIN_QUOTE_CHARS and q in _norm(job.description + " " + job.title) else None
    return None


def check(draft: DraftAnswer, q: Question, profile: Profile, job: JobPosting) -> list[Issue]:
    issues: list[Issue] = []

    def bad(code: str, msg: str, sentence: int | None = None) -> None:
        issues.append(Issue(key=q.key, code=code, message=msg, sentence=sentence))

    if draft.action == "skip":
        return issues  # a skip is always allowed; it goes to the user

    if draft.action == "choose":
        if q.kind not in ("single_choice", "multi_choice"):
            bad("wrong_action", f"'{q.label}' takes text, not a choice.")
        unknown = [c for c in draft.choices if c not in q.options]
        if unknown:
            bad("unknown_option", f"Not an option on the form: {unknown}. Options: {q.options}")
        if not draft.choices:
            bad("empty", "No option chosen.")
        if q.kind == "single_choice" and len(draft.choices) > 1:
            bad("too_many", "Only one option may be chosen.")
        if not draft.choice_sources:
            bad("uncited", "The choice cites no source.")
        for s in draft.choice_sources:
            if source_text(s.kind, s.ref, profile, job) is None:
                bad("bad_source", f"Source doesn't resolve: {s.kind}:{s.ref}")
        return issues

    # action == "write"
    if q.kind not in ("short_text", "long_text"):
        bad("wrong_action", f"'{q.label}' is a {q.kind} field; it can't take written text.")
    if not draft.sentences:
        bad("empty", "No text written.")

    for i, s in enumerate(draft.sentences):
        if not s.sources:
            bad("uncited", f"Sentence has no source: “{s.text}”", i)
            continue
        texts = []
        for src in s.sources:
            t = source_text(src.kind, src.ref, profile, job)
            if t is None:
                what = "quote isn't in the posting" if src.kind == "posting" else "doesn't exist"
                bad("bad_source", f"{src.kind}:{src.ref[:60]} {what}.", i)
            else:
                texts.append(t)
        missing = _numbers(s.text) - set().union(*(_numbers(t) for t in texts)) if texts else set()
        if missing:
            bad("unsupported_number", f"Numbers not in any cited source: {sorted(missing)}", i)

    text = draft.text
    for m in sorted({m.group(0).lower() for m in _BANNED.finditer(text)}):
        bad("banned_phrase", f"Generic phrase: “{m}”.")
    if "—" in text or " -- " in text:
        bad("style", "Uses an em dash; write two sentences instead.")
    if "!" in text:
        bad("style", "Uses an exclamation mark.")
    if re.search(r"\[[^\]]*\]|\{[^}]*\}|\bTODO\b|\bXX+\b", text):
        bad("placeholder", "Contains a placeholder.")
    limit = q.max_length or DEFAULT_MAX.get(q.kind, 1500)
    if len(text) > limit:
        bad("too_long", f"{len(text)} characters; the limit is {limit}.")
    return issues
