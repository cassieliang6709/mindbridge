"""Secret redaction applied before any transcript text is stored.

Transcripts routinely contain credentials — a pasted key, an env file read by a
tool, a token echoed by a shell command. The database is local, but "local" is
not a reason to persist a live API key in a second place, and a copied-out
digest should not carry one either.

This is a safety net, not a guarantee: it catches well-known key shapes. It
cannot catch an arbitrary secret that looks like ordinary text.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Provider keys with distinctive prefixes.
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("stripe-key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN[^-]{0,40}PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}")),
    # KEY=value where the name looks sensitive. Value may be quoted.
    (
        "env-assignment",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API[_-]?KEY|"
            r"ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)[A-Z0-9_]*)\s*[:=]\s*"
            r"(\"[^\"\n]{6,}\"|'[^'\n]{6,}'|[^\s\"',;]{6,})"
        ),
    ),
    # Postgres/Redis URLs carrying an inline password.
    (
        "dsn-password",
        re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s@/]{3,})@"),
    ),
]


def redact(text: str) -> tuple[str, int]:
    """Return the text with secrets masked, and how many were masked."""
    if not text:
        return text, 0

    count = 0

    def mask_env(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}=[redacted]"

    def mask_dsn(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}:[redacted]@"

    for name, pattern in _PATTERNS:
        if name == "env-assignment":
            text, hits = pattern.subn(mask_env, text)
        elif name == "dsn-password":
            text, hits = pattern.subn(mask_dsn, text)
        else:
            text, hits = pattern.subn(f"[redacted:{name}]", text)
            count += hits
            continue
        # subn already counted through the callbacks for the two above.
        del hits
    return text, count
