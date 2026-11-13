#!/usr/bin/env python3
"""Phase I Task 8 §12.1 — minimal deterministic secret scan (``make test-secrets``).

Greps the tree for real-credential patterns and fails on hits, enforcing the
brief's "no real credentials in the repo" constraint. Design:

* **Deterministic** — fixed pattern list, fixed walk, no network, no
  heuristics beyond the documented ones below.
* **Real-credential patterns only** — AWS access keys, GitHub tokens, Slack
  tokens, Stripe live keys, Google API keys, OpenAI keys, private-key blocks,
  and non-local database URLs that carry a password. Local-only defaults
  (``postgres:postgres@localhost`` etc.) are the documented dev stack and are
  NOT flagged.
* **Exclusions** — ``.git/``, ``.venv/``, caches, binary files, ``*.dump``
  and every ``.env`` file (the real secrets live there; the file itself is
  gitignored). ``.env.example`` is scanned (it must only contain placeholders).

Exit status: 0 when clean, 1 when hits are found (with file:line for each).

Usage:
    uv run python scripts/security/scan_secrets.py            # scan the tree
    uv run python scripts/security/scan_secrets.py --list     # list patterns
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Directories never scanned (build/cache/vendored).
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".import_linter_cache",
        ".hypothesis",
        "mutants",
        ".mutmut-cache",
        "__pycache__",
        "node_modules",
        "data",
        ".superpowers",
    }
)
#: Files never scanned (binary/serialized artifacts and real-secret homes).
SKIP_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".dylib",
        ".dump",
        ".sql",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".whl",
    }
)
#: Hosts that are local-only or the docker-compose service names: a URL with
#: credentials to one of these is the documented dev stack, not a secret.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "postgres", "redis", "db"})

#: (name, regex) — the real-credential patterns.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "aws_secret_key",
        re.compile(r"(?i)\baws_secret_access_key\b\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"),
    ),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_live_key", re.compile(r"\b(?:sk|rk)_live_[0-9a-zA-Z]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (
        "db_url_with_password",
        # Any scheme://user:pass@host where host is not local/dev and the
        # password differs from the username (the compose default).
        re.compile(
            r"\b(?:postgres|postgresql|mysql|redis|rediss|mongodb|amqp|amqps)://"
            r"([^:/\s@]+):([^@/\s]+)@([^/\s]+)"
        ),
    ),
]


def _db_url_is_local(match: re.Match[str]) -> bool:
    """True when a db URL's host is local-only or the password is a default."""
    user, password, host = match.groups()
    hostname = host.split(":")[0].strip("[]")
    if hostname in _LOCAL_HOSTS:
        return True
    # A password identical to the username (postgres:postgres) is a default.
    return password == user or password in {"postgres", "password", "changeme"}


def _matches(text: str) -> list[tuple[str, str]]:
    """All ``(kind, matched_text)`` hits for ``text`` (URLs pre-filtered)."""
    hits: list[tuple[str, str]] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            if kind == "db_url_with_password" and _db_url_is_local(match):
                continue
            hits.append((kind, match.group(0)))
    return hits


def _iter_targets(root: Path = ROOT) -> list[Path]:
    targets: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        name = path.name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            continue  # the real secrets live here; gitignored, never scanned
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue  # binary
        targets.append(path)
    return targets


def scan(root: Path = ROOT) -> list[tuple[Path, str, str]]:
    """Return ``(file, kind, matched_text)`` for every hit in ``root``."""
    findings: list[tuple[Path, str, str]] = []
    for path in _iter_targets(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for kind, matched in _matches(text):
            findings.append((path, kind, matched))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the pattern inventory and exit")
    args = parser.parse_args(argv)

    if args.list:
        print("secret-scan pattern inventory (make test-secrets):")
        for kind, pattern in PATTERNS:
            print(f"  {kind:<22} {pattern.pattern}")
        print("\nExcluded: .git/ .venv/ caches, binary/serialized files, *.dump, all .env files.")
        return 0

    findings = scan()
    if not findings:
        print("secret scan: clean (no real-credential patterns found)")
        return 0
    print(f"secret scan: {len(findings)} hit(s) — real-credential pattern found:")
    for path, kind, matched in findings:
        rel = path.relative_to(ROOT)
        line = "?"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            for lineno, text in enumerate(content.splitlines(), 1):
                if matched.splitlines()[0] in text:
                    line = str(lineno)
                    break
        except OSError:
            pass
        print(f"  {rel}:{line} [{kind}] {matched[:80]}")
    print("\nRefusing to pass: fix the hit (remove the credential / rotate it) or, if it is")
    print("a deliberate fixture, add it to the exclusion list in scripts/security/scan_secrets.py.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
