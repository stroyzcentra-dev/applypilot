"""The Writer and Verifier agents.

Writer: drafts answers to a form's open questions from the fact bank, the
standard answers and the posting. Every sentence cites its sources.

Verifier: two layers.
  1. `checks.check`, plain code: sources resolve, posting quotes are verbatim,
     numbers appear in a cited source, no stock phrases, length limits.
  2. An LLM critic that reads each sentence next to the text of its sources
     and judges whether the claim actually follows and says something specific.

Answers that fail get one rewrite with the exact problems listed. Anything
still failing after that is left for the user, with the reasons.
"""

from __future__ import annotations

import json
import logging

from applypilot.llm import LLM
from applypilot.models import JobPosting, Profile
from applypilot.writer.checks import BANNED_PHRASES, check, source_text
from applypilot.writer.models import (
    Critique,
    DraftAnswer,
    Drafts,
    Issue,
    Question,
    VerifiedAnswer,
)

log = logging.getLogger(__name__)

MAX_POSTING_CHARS = 16_000
MAX_ROUNDS = 2  # first draft + one rewrite

WRITER_SYSTEM = """\
You are the Writer in a job application pipeline. You draft answers to the open \
questions on one application form, in the candidate's voice, for them to send.

You know only what is below. Never add experience, skills, numbers, opinions or \
feelings the sources don't state.

<fact_bank>
{facts}
</fact_bank>

<standard_answers>
{answers}
</standard_answers>

For each question, choose one action:
- write: for short_text and long_text questions you can answer honestly from the \
sources. Write in first person. Each sentence lists its sources:
    fact:<id> for a fact, answer:<key> for a standard answer,
    posting:"<exact quote>" for something the posting says (copy 12+ characters \
verbatim; never paraphrase inside a quote).
  A sentence about the company or role must cite the posting, and its quotes must \
contain everything the sentence says about them: quote whole phrases or the full \
list, never stop a quote mid-list and then claim the rest. A sentence about the \
candidate must cite a fact or answer. Any number must appear in a cited source.
- choose: for single_choice and multi_choice questions. Copy option labels exactly. \
Cite what the choice rests on (usually an answer:<key>).
- skip: when the sources don't support an honest answer, and always for: \
consent to marketing, SMS or WhatsApp messages; legal attestations and signatures; \
criminal history; salary when answer:salary_expectation is missing; voluntary \
demographic questions unless answer:self_identification says to decline; file \
uploads; typeahead fields. Give a one-line reason the user will see.

Style for written answers:
- Plain, specific sentences. Name the system, technology, scale or result.
- Answer what was asked, then stop. Every sentence must serve the question; don't \
pad with experience that's unrelated to it. long_text: 2 to 5 sentences, or 3 short \
paragraphs of 2-3 sentences for a cover letter. short_text: one sentence or a phrase.
- Respect max_length.
- Never use these words or phrases: {banned}.
- No em dashes, no exclamation marks, no rhetorical questions, no flattery of \
the company, no closing pleasantries.
"""

CRITIC_SYSTEM = """\
You are the Verifier in a job application pipeline. You check drafted answers \
before they are sent under the candidate's name. You are strict: a sentence that \
overstates its sources would embarrass the candidate in an interview.

For each sentence you get the text of every source it cites. Judge:
- supported: every claim in the sentence follows from those source texts. \
Rewording is fine. Inflating scope ("led" from "built", "years" from months, \
"expert" from "used"), adding results, motives or feelings is not. Neither is \
embellishment the source doesn't state, however small: "from the ground up", \
"end to end", "production-grade", "robust", "scalable", "complex", "critical", \
"high-traffic", "single-handedly".
- specific: the sentence says something concrete about this candidate or this \
role. Filler that would fit any application is not specific.
And per answer: answers_the_question, i.e. it responds to what was asked.

When something fails, the note says exactly what, in one sentence, so the \
Writer can fix it.
"""


def question_block(questions: list[Question]) -> str:
    return json.dumps([q.model_dump(exclude_none=True) for q in questions], indent=1)


class Writer:
    def __init__(self, llm: LLM, profile: Profile):
        self.llm = llm
        self.profile = profile
        answers = profile.answer_sources
        self._system = WRITER_SYSTEM.format(
            facts=profile.facts_as_prompt(),
            answers="\n".join(f"[{k}] {v}" for k, v in answers.items()) or "(none)",
            banned=", ".join(f'"{p}"' for p in BANNED_PHRASES),
        )

    def draft(
        self,
        job: JobPosting,
        questions: list[Question],
        feedback: dict[str, list[Issue]] | None = None,
        previous: dict[str, DraftAnswer] | None = None,
    ) -> list[DraftAnswer]:
        user = (
            f"<posting>\nCompany: {job.company}\nTitle: {job.title}\n\n"
            f"{job.description[:MAX_POSTING_CHARS]}\n</posting>\n\n"
            f"<questions>\n{question_block(questions)}\n</questions>\n"
        )
        if feedback:
            notes = []
            for key, issues in feedback.items():
                prev = previous.get(key) if previous else None
                notes.append(
                    f"Question {key}. Previous draft: {prev.model_dump_json() if prev else '(none)'}\n"
                    + "\n".join(f"- {i.message}" for i in issues)
                )
            user += (
                "\nThese are rewrites. Your previous drafts failed review for the reasons below. "
                "Fix every problem: delete or rephrase the exact words flagged, and drop any "
                "sentence the answer doesn't need. Skip the question if it can't be answered "
                "honestly.\n\n"
                + "\n\n".join(notes)
            )
        user += "\nReturn one answer per question."
        drafts = self.llm.structured(system=self._system, user=user, schema=Drafts, effort="high")
        return drafts.answers


class Critic:
    def __init__(self, llm: LLM, profile: Profile):
        self.llm = llm
        self.profile = profile

    def review(self, job: JobPosting, items: list[tuple[Question, DraftAnswer]]) -> list[Issue]:
        if not items:
            return []
        blocks = []
        for q, d in items:
            lines = [f"<answer key={json.dumps(q.key)} question={json.dumps(q.label)}>"]
            for i, s in enumerate(d.sentences):
                lines.append(f"  [{i}] {s.text}")
                for src in s.sources:
                    t = source_text(src.kind, src.ref, self.profile, job) or "(unresolved)"
                    lines.append(f"      source {src.kind}:{src.ref[:40]} -> {t}")
            lines.append("</answer>")
            blocks.append("\n".join(lines))
        critique = self.llm.structured(
            system=CRITIC_SYSTEM,
            user="\n\n".join(blocks) + "\n\nReview every sentence of every answer.",
            schema=Critique,
            effort="medium",
        )
        issues: list[Issue] = []
        for r in critique.reviews:
            if not r.answers_the_question:
                issues.append(Issue(key=r.key, code="off_question",
                                    message=r.note or "Doesn't answer the question asked."))
            for s in r.sentences:
                if not s.supported:
                    issues.append(Issue(key=r.key, code="overstated", sentence=s.index,
                                        message=s.note or "Claims more than its sources say."))
                elif not s.specific:
                    issues.append(Issue(key=r.key, code="generic", sentence=s.index,
                                        message=s.note or "Too generic."))
        return issues


def draft_and_verify(
    writer: Writer, critic: Critic, job: JobPosting, questions: list[Question]
) -> list[VerifiedAnswer]:
    """Draft, check, critique, rewrite once. Returns one result per question."""
    by_key = {q.key: q for q in questions}
    results: dict[str, VerifiedAnswer] = {}
    pending = list(questions)
    feedback: dict[str, list[Issue]] = {}
    previous: dict[str, DraftAnswer] = {}

    for round_no in range(1, MAX_ROUNDS + 1):
        drafts = {d.key: d for d in writer.draft(job, pending, feedback or None, previous or None)}
        issues: dict[str, list[Issue]] = {}
        for q in pending:
            d = drafts.get(q.key) or DraftAnswer(key=q.key, action="skip",
                                                 skip_reason="The Writer returned no answer.")
            drafts[q.key] = d
            issues[q.key] = check(d, q, writer.profile, job)

        # Only spend a critic call on written answers that passed the hard checks.
        to_review = [(q, drafts[q.key]) for q in pending
                     if drafts[q.key].action == "write" and not issues[q.key]]
        for i in critic.review(job, to_review):
            if i.key in issues:
                issues[i.key].append(i)

        failed: list[Question] = []
        for q in pending:
            ok = not issues[q.key]
            results[q.key] = VerifiedAnswer(question=q, draft=drafts[q.key], passed=ok,
                                            issues=issues[q.key], rounds=round_no)
            if not ok:
                failed.append(q)
        if not failed:
            break
        pending = failed
        feedback = {q.key: issues[q.key] for q in failed}
        previous = {q.key: drafts[q.key] for q in failed}

    return [results[k] for k in by_key]
