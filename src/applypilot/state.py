"""The application lifecycle as an explicit state machine.

Every status change goes through `check_transition`, so the log can't record
an impossible history (say, `submitted` straight from `discovered`).
"""

from __future__ import annotations

from applypilot.models import ApplicationStatus as S


class InvalidTransition(ValueError):
    pass


TRANSITIONS: dict[S, frozenset[S]] = {
    # SUBMITTED from early states: you applied on your own, outside ApplyPilot.
    # DRAFTING directly from DISCOVERED: you chose to apply before it was scored.
    S.DISCOVERED: frozenset({S.FILTERED_OUT, S.SCORED, S.FAILED, S.DRAFTING, S.SUBMITTED}),
    S.FILTERED_OUT: frozenset({S.DISCOVERED, S.SUBMITTED}),
    S.SCORED: frozenset({S.BELOW_THRESHOLD, S.DRAFTING, S.SUBMITTED}),
    S.BELOW_THRESHOLD: frozenset({S.DRAFTING, S.SUBMITTED}),
    S.DRAFTING: frozenset({S.AWAITING_APPROVAL, S.SUBMITTING, S.FAILED, S.SUBMITTED}),
    # SUBMITTED directly: the user submitted it themselves (handoff or manual click).
    S.AWAITING_APPROVAL: frozenset({S.SUBMITTING, S.SUBMITTED, S.DRAFTING}),
    S.SUBMITTING: frozenset({S.SUBMITTED, S.FAILED}),
    S.FAILED: frozenset({S.DISCOVERED, S.DRAFTING, S.SUBMITTING, S.SUBMITTED}),
    S.SUBMITTED: frozenset({S.INTERVIEWING, S.REJECTED, S.OFFER}),
    S.INTERVIEWING: frozenset({S.OFFER, S.REJECTED}),
    S.REJECTED: frozenset(),
    S.OFFER: frozenset(),
    S.WITHDRAWN: frozenset(),
}

# The user can always withdraw from anything that isn't already final.
TERMINAL = frozenset({S.REJECTED, S.OFFER, S.WITHDRAWN})


def allowed(src: S) -> frozenset[S]:
    extra = frozenset() if src in TERMINAL else frozenset({S.WITHDRAWN})
    return TRANSITIONS[src] | extra


def check_transition(src: S, dst: S) -> None:
    if dst not in allowed(src):
        options = ", ".join(sorted(allowed(src))) or "none (final state)"
        raise InvalidTransition(f"can't go from {src} to {dst}; allowed: {options}")
