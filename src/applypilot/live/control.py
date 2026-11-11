"""Who is driving the browser right now: the agent or the user.

The agent calls `checkpoint()` before every action. If the user paused the run
or took over, the agent waits there until control is handed back, so it never
acts on a page the user is in the middle of using.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Literal


class Mode(StrEnum):
    AGENT = "agent"  # agent is acting
    PAUSED = "paused"  # agent waits; nobody is acting
    USER = "user"  # user is driving the live browser
    WAITING_APPROVAL = "waiting_approval"  # form is ready; waiting for the user's decision
    DONE = "done"


class RunCancelled(Exception):
    pass


Decision = Literal["agent_submit", "user_submitted", "open_page", "cancel"]


class RunControl:
    def __init__(self) -> None:
        self.mode = Mode.AGENT
        self._resume = asyncio.Event()
        self._resume.set()
        self._cancelled = False
        self._decision: asyncio.Future[Decision] | None = None

    # ---- called by the agent --------------------------------------------

    async def checkpoint(self) -> None:
        await self._resume.wait()
        if self._cancelled:
            raise RunCancelled

    async def await_decision(self) -> Decision:
        self.mode = Mode.WAITING_APPROVAL
        self._decision = asyncio.get_running_loop().create_future()
        try:
            return await self._decision
        finally:
            self._decision = None

    def finish(self) -> None:
        self.mode = Mode.DONE
        self._resume.set()

    # ---- called by the user (dashboard) ---------------------------------

    def pause(self) -> None:
        self._hold(Mode.PAUSED)

    def take_over(self) -> None:
        self._hold(Mode.USER)

    def hand_back(self) -> None:
        if self.mode in (Mode.PAUSED, Mode.USER):
            self.mode = Mode.AGENT
            self._resume.set()

    def decide(self, decision: Decision) -> bool:
        if self._decision is None or self._decision.done():
            return False
        self._decision.set_result(decision)
        return True

    def cancel(self) -> None:
        self._cancelled = True
        if not self.decide("cancel"):
            self._resume.set()

    def _hold(self, mode: Mode) -> None:
        if self.mode == Mode.AGENT:
            self.mode = mode
            self._resume.clear()
        elif self.mode in (Mode.PAUSED, Mode.USER):
            self.mode = mode
