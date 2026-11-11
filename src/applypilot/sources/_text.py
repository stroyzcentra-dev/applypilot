"""Helpers for cleaning posting text and parsing fields."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime

from bs4 import BeautifulSoup

_WS = re.compile("[ \\t\\u00a0]+")  # spaces, tabs, non-breaking spaces
_BLANKS = re.compile(r"\n{3,}")
_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)


def html_to_text(raw: str | None, *, escaped: bool = False) -> str:
    """Turn posting HTML into readable plain text, keeping paragraph and list breaks."""
    if not raw:
        return ""
    if escaped:
        raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for tag in soup.find_all(["p", "br", "div", "h1", "h2", "h3", "h4", "ul", "ol"]):
        tag.insert_after("\n")
    text = soup.get_text()
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def looks_remote(*values: str | None) -> bool:
    return any(v and _REMOTE.search(v) for v in values)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def from_epoch_ms(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, tz=UTC) if value else None
