"""The step timeline shown next to the live browser.

Each run has its own bus. The dashboard subscribes over server-sent events and
first receives the history, so opening the page mid-run still shows every step.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

StepKind = Literal[
    "action", "info", "warning", "handoff", "needs_input", "status", "error", "draft", "draft_failed"
]


class Step(BaseModel):
    seq: int
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    kind: StepKind
    title: str
    detail: str = ""
    url: str | None = None
    screenshot: str | None = None  # path relative to the screenshots mount
    # For drafted answers: each sentence with the sources it cites, for the dashboard.
    sentences: list[dict] | None = None
    issues: list[str] | None = None
    file: str | None = None  # a generated document, relative to the resumes mount


class EventBus:
    def __init__(self) -> None:
        self.history: list[Step] = []
        self._subscribers: set[asyncio.Queue[Step | None]] = set()
        self._closed = False

    def emit(self, kind: StepKind, title: str, **kw) -> Step:
        step = Step(seq=len(self.history) + 1, kind=kind, title=title, **kw)
        self.history.append(step)
        for q in self._subscribers:
            q.put_nowait(step)
        return step

    def close(self) -> None:
        self._closed = True
        for q in self._subscribers:
            q.put_nowait(None)

    async def subscribe(self) -> AsyncIterator[Step]:
        q: asyncio.Queue[Step | None] = asyncio.Queue()
        backlog = list(self.history)
        self._subscribers.add(q)
        try:
            for step in backlog:
                yield step
            if self._closed:
                return
            while (step := await q.get()) is not None:
                yield step
        finally:
            self._subscribers.discard(q)
