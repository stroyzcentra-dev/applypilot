"""Types for drafted answers. Every sentence carries the sources it rests on."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QuestionKind = Literal[
    "short_text", "long_text", "single_choice", "multi_choice", "typeahead", "file"
]


class Question(BaseModel):
    """One form field the Writer is asked to answer."""

    key: str
    label: str
    kind: QuestionKind
    required: bool
    options: list[str] = Field(default_factory=list)
    max_length: int | None = None


class Source(BaseModel):
    kind: Literal["fact", "answer", "posting"]
    ref: str = Field(
        description=(
            "fact: a fact ID from the fact bank. answer: a key from the standard answers. "
            "posting: a short verbatim quote (under 25 words) copied exactly from the posting."
        )
    )


class Sentence(BaseModel):
    text: str
    sources: list[Source] = Field(description="Every source this sentence's claims rest on.")


class DraftAnswer(BaseModel):
    key: str = Field(description="The question's key, copied exactly.")
    action: Literal["write", "choose", "skip"]
    sentences: list[Sentence] = Field(
        default_factory=list, description="For action=write: the answer, one claim-bearing sentence each."
    )
    choices: list[str] = Field(
        default_factory=list, description="For action=choose: option labels copied exactly."
    )
    choice_sources: list[Source] = Field(
        default_factory=list, description="For action=choose: what the choice is based on."
    )
    skip_reason: str = Field("", description="For action=skip: why the user should answer this.")

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.sentences)


class Drafts(BaseModel):
    answers: list[DraftAnswer]


# ---- critic ----------------------------------------------------------------


class SentenceReview(BaseModel):
    index: int
    supported: bool = Field(description="Every claim in the sentence follows from its sources.")
    specific: bool = Field(description="Says something concrete; not filler a template could produce.")
    note: str = Field("", description="If either is false: what exactly is wrong, in one sentence.")


class AnswerReview(BaseModel):
    key: str
    answers_the_question: bool
    sentences: list[SentenceReview]
    note: str = ""


class Critique(BaseModel):
    reviews: list[AnswerReview]


# ---- verified result ---------------------------------------------------------


class Issue(BaseModel):
    key: str
    code: str
    message: str
    sentence: int | None = None


class VerifiedAnswer(BaseModel):
    question: Question
    draft: DraftAnswer
    passed: bool
    issues: list[Issue] = Field(default_factory=list)
    rounds: int = 1
