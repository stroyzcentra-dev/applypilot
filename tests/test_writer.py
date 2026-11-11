from types import SimpleNamespace

import pytest

from applypilot.llm import LLM
from applypilot.models import JobPosting
from applypilot.writer import Critic, Writer, draft_and_verify
from applypilot.writer.checks import check
from applypilot.writer.models import (
    AnswerReview,
    Critique,
    DraftAnswer,
    Drafts,
    Question,
    Sentence,
    SentenceReview,
    Source,
)

JOB = JobPosting(
    source="greenhouse",
    external_id="1",
    company="acme",
    title="Backend Engineer",
    url="https://x",
    description="You will build the APIs behind our freight marketplace, "
    "which quotes 2 million shipments a year.",
)
WHY = Question(key="q1", label="Why do you want to work here?", kind="long_text", required=True)
RELO = Question(
    key="q2", label="Are you open to relocating?", kind="single_choice", required=True, options=["Yes", "No"]
)


def fact(ref):
    return Source(kind="fact", ref=ref)


def write(*sentences) -> DraftAnswer:
    return DraftAnswer(
        key="q1", action="write", sentences=[Sentence(text=t, sources=s) for t, s in sentences]
    )


def codes(issues):
    return sorted(i.code for i in issues)


# ---- deterministic checks --------------------------------------------------------


def test_grounded_answer_passes(profile):
    d = write(
        ("I cut p95 latency on a quote service from 900ms to 180ms.", [fact("exp-acme-latency")]),
        (
            "You're building APIs behind a freight marketplace.",
            [Source(kind="posting", ref="build the APIs behind our freight marketplace")],
        ),
    )
    assert check(d, WHY, profile, JOB) == []


def test_invented_fact_and_misquote(profile):
    d = write(
        ("I led a team of five.", [fact("exp-made-up")]),
        ("You ship 3 million shipments.", [Source(kind="posting", ref="quotes 3 million shipments")]),
    )
    assert codes(check(d, WHY, profile, JOB)) == ["bad_source", "bad_source"]


def test_number_must_come_from_a_cited_source(profile):
    d = write(("I cut latency by 95% on a 40k request/day service.", [fact("exp-acme-api")]))
    issues = check(d, WHY, profile, JOB)
    assert codes(issues) == ["unsupported_number"] and "95" in issues[0].message


def test_style_lint(profile):
    d = write(("I'm passionate about APIs — and I leverage FastAPI daily!", [fact("exp-acme-api")]))
    assert codes(check(d, WHY, profile, JOB)) == [
        "banned_phrase",
        "banned_phrase",
        "style",
        "style",
    ]


def test_uncited_and_too_long(profile):
    q = WHY.model_copy(update={"max_length": 20})
    d = write(("I built a FastAPI service for pricing freight quotes.", []))
    assert codes(check(d, q, profile, JOB)) == ["too_long", "uncited"]


def test_choice_must_be_a_real_option_with_a_source(profile):
    ok = DraftAnswer(
        key="q2",
        action="choose",
        choices=["No"],
        choice_sources=[Source(kind="answer", ref="willing_to_relocate")],
    )
    assert check(ok, RELO, profile, JOB) == []
    bad = DraftAnswer(key="q2", action="choose", choices=["Maybe"])
    assert codes(check(bad, RELO, profile, JOB)) == ["uncited", "unknown_option"]


def test_answer_sources_include_authorization_and_standard_answers(profile):
    a = profile.answer_sources
    assert a["work_authorization"] == "US citizen" and a["needs_sponsorship"] == "No"
    assert a["willing_to_relocate"] == "No" and a["notice_period"] == "Two weeks"
    assert "salary_expectation" not in a  # left blank, so it can't be cited


# ---- draft, check, critique, rewrite ------------------------------------------------


class ScriptedLLM(LLM):
    """Returns queued responses per schema, and records every request."""

    def __init__(self, script: dict):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.model = "fake"

    def structured(self, *, system, user, schema, **kw):
        self.calls.append(SimpleNamespace(schema=schema, user=user))
        return self.script[schema].pop(0)


def test_rewrite_fixes_a_failed_draft(profile):
    bad = write(("I'm passionate about freight.", [fact("exp-acme-api")]))
    good = write(("I built a FastAPI service that prices freight quotes.", [fact("exp-acme-api")]))
    relo = DraftAnswer(
        key="q2",
        action="choose",
        choices=["No"],
        choice_sources=[Source(kind="answer", ref="willing_to_relocate")],
    )
    ok_review = Critique(
        reviews=[
            AnswerReview(
                key="q1",
                answers_the_question=True,
                sentences=[SentenceReview(index=0, supported=True, specific=True)],
            )
        ]
    )
    llm = ScriptedLLM({Drafts: [Drafts(answers=[bad, relo]), Drafts(answers=[good])], Critique: [ok_review]})

    results = draft_and_verify(Writer(llm, profile), Critic(llm, profile), JOB, [WHY, RELO])

    assert [r.passed for r in results] == [True, True]
    assert results[0].rounds == 2 and results[0].draft.text.startswith("I built")
    rewrite_request = llm.calls[1].user
    assert "Generic phrase" in rewrite_request and "Previous draft" in rewrite_request
    # The critic only saw the draft that passed the hard checks, and only once.
    assert [c.schema for c in llm.calls] == [Drafts, Drafts, Critique]


def test_critic_rejection_leaves_it_for_the_user(profile):
    overstated = write(("I led backend architecture at Acme.", [fact("exp-acme-api")]))
    no = Critique(
        reviews=[
            AnswerReview(
                key="q1",
                answers_the_question=True,
                sentences=[
                    SentenceReview(
                        index=0, supported=False, specific=True, note="'led architecture' isn't in the fact"
                    )
                ],
            )
        ]
    )
    llm = ScriptedLLM({Drafts: [Drafts(answers=[overstated])] * 2, Critique: [no, no]})

    [result] = draft_and_verify(Writer(llm, profile), Critic(llm, profile), JOB, [WHY])

    assert not result.passed and result.rounds == 2
    assert result.issues[0].code == "overstated"


def test_writer_prompt_carries_facts_answers_and_banned_list(profile):
    llm = ScriptedLLM({Drafts: [Drafts(answers=[])]})
    w = Writer(llm, profile)
    w.draft(JOB, [WHY])
    assert "[exp-acme-latency]" in w._system and "[willing_to_relocate] No" in w._system
    assert '"passionate"' in w._system
    assert "Why do you want to work here?" in llm.calls[0].user


@pytest.mark.parametrize("phrase", ["aligns with", "Truly", "hit the ground running"])
def test_banned_phrases_are_case_insensitive(profile, phrase):
    d = write((f"My work {phrase} the role.", [fact("exp-acme-api")]))
    assert "banned_phrase" in codes(check(d, WHY, profile, JOB))
