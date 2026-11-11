"""Site automation policy: may the agent drive this site, and what should the user be told?

Every page the agent navigates to passes through `decide()`. The browser session
enforces the result, so no agent can skip the check by accident.

Policies are our reading of each site's published terms, kept short with a link
to the source. They are not legal advice; check the linked terms yourself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class Level(StrEnum):
    ALLOWED = "allowed"  # employer-hosted application forms; filling them is the intended use
    CAUTION = "caution"  # no policy on file, or extra friction (accounts, CAPTCHAs)
    PROHIBITED = "prohibited"  # the site's terms forbid bots or automated access


class Action(StrEnum):
    AUTOMATE = "automate"  # agent drives the browser
    AUTOMATE_NO_AUTOSUBMIT = "automate_no_autosubmit"  # agent drives, but a human must submit
    HANDOFF = "handoff"  # agent stops; the user drives the live browser


@dataclass(frozen=True)
class SitePolicy:
    domain: str
    level: Level
    reason: str
    terms_url: str | None = None


_ATS_REASON = (
    "Employer-hosted application form. Filling it in is the intended use, and we know of "
    "no term against candidates using tools. CAPTCHAs are always handed to you."
)

REGISTRY: tuple[SitePolicy, ...] = (
    SitePolicy("greenhouse.io", Level.ALLOWED, _ATS_REASON),
    SitePolicy("lever.co", Level.ALLOWED, _ATS_REASON),
    SitePolicy("ashbyhq.com", Level.ALLOWED, _ATS_REASON),
    SitePolicy(
        "myworkdayjobs.com",
        Level.CAUTION,
        "Workday makes you create an account per employer. You create accounts and sign "
        "in yourself; the agent can fill the form after that.",
    ),
    SitePolicy(
        "linkedin.com",
        Level.PROHIBITED,
        "LinkedIn's User Agreement forbids using bots or other automated methods to access "
        "the service, and LinkedIn restricts accounts it catches doing so.",
        "https://www.linkedin.com/legal/user-agreement",
    ),
    SitePolicy(
        "indeed.com",
        Level.PROHIBITED,
        "Indeed's terms forbid accessing the site through automated means such as bots "
        "or scrapers.",
        "https://www.indeed.com/legal",
    ),
    SitePolicy(
        "glassdoor.com",
        Level.PROHIBITED,
        "Glassdoor's terms forbid automated access to the site.",
        "https://www.glassdoor.com/about/terms.htm",
    ),
)

UNKNOWN_REASON = "No policy on file for this site. Check its terms before automating it."


@dataclass(frozen=True)
class Decision:
    host: str
    policy: SitePolicy
    action: Action
    warning: str | None  # shown to the user in the dashboard and written to the log

    @property
    def may_automate(self) -> bool:
        return self.action != Action.HANDOFF

    @property
    def may_autosubmit(self) -> bool:
        return self.action == Action.AUTOMATE


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def lookup(url: str) -> SitePolicy:
    host = host_of(url)
    for p in REGISTRY:
        if host == p.domain or host.endswith("." + p.domain):
            return p
    return SitePolicy(host, Level.CAUTION, UNKNOWN_REASON)


def _matches(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in (d.lower() for d in domains))


def decide(url: str, accept_tos_risk_for: list[str] | None = None) -> Decision:
    """What the agent may do on `url`.

    `accept_tos_risk_for` lists domains where the user has explicitly chosen to automate
    despite the site's terms. They still get a warning every time, and still submit by hand.
    """
    host = host_of(url)
    policy = lookup(url)
    terms = f" Terms: {policy.terms_url}" if policy.terms_url else ""

    if policy.level == Level.ALLOWED:
        return Decision(host, policy, Action.AUTOMATE, None)

    if policy.level == Level.CAUTION:
        return Decision(
            host, policy, Action.AUTOMATE_NO_AUTOSUBMIT,
            f"Caution on {host}: {policy.reason} The agent will fill the form but won't submit it.",
        )

    if _matches(host, accept_tos_risk_for or []):
        return Decision(
            host, policy, Action.AUTOMATE_NO_AUTOSUBMIT,
            f"{host} prohibits automation. You chose to accept that risk for this site, so the "
            f"agent will continue, but your account could be restricted. {policy.reason}{terms}",
        )

    return Decision(
        host, policy, Action.HANDOFF,
        f"{host} prohibits automation, so the agent has stopped and handed the browser to you. "
        f"{policy.reason}{terms}",
    )
