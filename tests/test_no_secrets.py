"""Fails if any tracked file contains something shaped like a credential.

Runs locally with the rest of the suite and in CI on every push, alongside
GitHub's own secret scanning and push protection.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PATTERNS = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Filled-in API key variable": re.compile(r"^\s*ANTHROPIC_API_KEY\s*=\s*\S+", re.MULTILINE),
}
SKIP_SUFFIXES = {".lock", ".png", ".jpg", ".pdf", ".ico"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [REPO / f for f in out.stdout.splitlines() if Path(f).suffix not in SKIP_SUFFIXES]


def test_no_credentials_in_tracked_files():
    hits = []
    for path in tracked_files():
        if path.name == "test_no_secrets.py" or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{path.relative_to(REPO)}: {name}" for name, rx in PATTERNS.items() if rx.search(text)]
    assert not hits, "Possible credentials committed:\n" + "\n".join(hits)


def test_env_files_are_ignored():
    for name in (".env", ".env.local", ".env.production", "profile.yaml", "settings.yaml",
                 "profile.yaml.bak", "settings.yaml.bak"):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO)
        assert r.returncode == 0, f"{name} isn't git-ignored; it holds personal data"
    assert subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=REPO).returncode == 1
