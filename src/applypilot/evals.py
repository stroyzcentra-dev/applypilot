"""Eval suite for the Writer + Verifier.

Each case is a synthetic posting with questions, and for each question the
honest outcome given the example profile: `answer` (the sources support it) or
`skip` (they don't, or it's the user's decision). The scorecard measures:

  correct        final outcome matches the expectation
  overclaimed    answered something it should have left alone (the worst error)
  missed         left something it could have answered
  first-pass     verified answers that needed no rewrite
  lint hits      banned phrases in answers that passed (should always be 0)

Runs against the live model, so it costs money: `applypilot eval`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from applypilot.models import JobPosting, Profile
from applypilot.writer import Critic, Question, VerifiedAnswer, Writer, draft_and_verify
from applypilot.writer.checks import _BANNED


@dataclass
class QuestionResult:
    case: str
    label: str
    expect: str
    outcome: str  # answered | skipped | rejected
    verdict: str  # correct | overclaimed | missed
    rounds: int
    text: str
    issues: list[str]


@dataclass
class Scorecard:
    results: list[QuestionResult] = field(default_factory=list)

    def summary(self) -> dict:
        r = self.results
        answered = [x for x in r if x.outcome == "answered"]
        n = len(r) or 1
        return {
            "questions": len(r),
            "correct": sum(x.verdict == "correct" for x in r) / n,
            "overclaimed": sum(x.verdict == "overclaimed" for x in r),
            "missed": sum(x.verdict == "missed" for x in r),
            "first_pass": (sum(x.rounds == 1 for x in answered) / len(answered)) if answered else 0.0,
            "lint_hits": sum(len(_BANNED.findall(x.text)) for x in answered),
        }


def load_case(path: Path) -> tuple[JobPosting, list[Question], list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    job = JobPosting(source="greenhouse", external_id=path.stem, company=data["company"],
                     title=data["title"], url="https://example.invalid", description=data["description"])
    questions, expects = [], []
    for i, q in enumerate(data["questions"]):
        questions.append(Question(key=f"q{i}", label=q["label"], kind=q["kind"], required=True,
                                  options=q.get("options", [])))
        expects.append(q["expect"])
    return job, questions, expects


def grade(case: str, expect: str, v: VerifiedAnswer) -> QuestionResult:
    d = v.draft
    if d.action == "skip":
        outcome = "skipped"
    elif v.passed:
        outcome = "answered"
    else:
        outcome = "rejected"  # the Verifier stopped it; it goes to the user like a skip
    if expect == "answer":
        verdict = "correct" if outcome == "answered" else "missed"
    else:
        verdict = "overclaimed" if outcome == "answered" else "correct"
    return QuestionResult(
        case=case, label=v.question.label, expect=expect, outcome=outcome, verdict=verdict,
        rounds=v.rounds, text=d.text or ", ".join(d.choices) or d.skip_reason,
        issues=[i.message for i in v.issues],
    )


def run_evals(cases_dir: Path, profile: Profile, writer: Writer, critic: Critic,
              out_dir: Path) -> tuple[Scorecard, Path]:
    card = Scorecard()
    for path in sorted(cases_dir.glob("*.yaml")):
        job, questions, expects = load_case(path)
        verified = draft_and_verify(writer, critic, job, questions)
        card.results += [grade(path.stem, e, v) for e, v in zip(expects, verified, strict=True)]

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"{stamp}.json"
    out.write_text(json.dumps({"summary": card.summary(),
                               "results": [asdict(r) for r in card.results]}, indent=2),
                   encoding="utf-8")
    return card, out
