"""Secret redaction applied before any transcript text is stored.

Transcripts routinely contain credentials — a pasted key, an env file read by a
tool, a token echoed by a shell command. The database is local, but "local" is
not a reason to persist a live API key in a second place, and a copied-out
digest should not carry one either.

This is a safety net, not a guarantee: it catches well-known key shapes. It
cannot catch an arbitrary secret that looks like ordinary text.

中文说明：在任何转录文本被写入数据库之前，先在这里做一遍密钥/凭证的脱敏处理。
转录内容里经常会混入凭证——粘贴的密钥、工具读到的 env 文件、shell 命令回显的
token。数据库虽然是本地的，但"本地"不是把一份活的 API key 再多存一份的理由，
导出的摘要文本同样不该带着密钥。请注意，这只是一道安全网而非绝对保证：它能
识别常见的密钥格式，但无法识别看起来像普通文本的任意秘密。
"""

from __future__ import annotations

import re

# Each entry is (name, pattern); name doubles as the placeholder text left in
# the redacted output, e.g. "[redacted:openai-key]".
# 中文：每一项是 (名称, 正则表达式)；名称同时会被用作脱敏后留下的占位符文本，
# 例如 "[redacted:openai-key]"。
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Provider keys with distinctive prefixes.
    # 中文：各服务商有独特前缀的密钥。
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
    # 中文：形如 KEY=value 的赋值，且变量名看起来是敏感信息；值可以带引号。
    (
        "env-assignment",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API[_-]?KEY|"
            r"ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)[A-Z0-9_]*)\s*[:=]\s*"
            r"(\"[^\"\n]{6,}\"|'[^'\n]{6,}'|[^\s\"',;]{6,})"
        ),
    ),
    # Postgres/Redis URLs carrying an inline password.
    # 中文：URL 里内联了密码的连接串，例如 Postgres 或 Redis 的 DSN。
    (
        "dsn-password",
        re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s@/]{3,})@"),
    ),
]


def redact(text: str) -> tuple[str, int]:
    """Return the text with secrets masked, and how many were masked.

    中文：返回脱敏后的文本，以及一共遮盖了多少处疑似密钥/凭证。

    Args:
        text: Raw text that may contain credentials. 可能包含凭证的原始文本。

    Returns:
        A tuple of (masked_text, redaction_count). 一个 (脱敏后文本, 脱敏次数) 元组。
    """
    if not text:
        return text, 0

    count = 0

    def mask_env(match: re.Match[str]) -> str:
        """Keep the variable name, replace only the value. 保留变量名，只替换值。"""
        nonlocal count
        count += 1
        return f"{match.group(1)}=[redacted]"

    def mask_dsn(match: re.Match[str]) -> str:
        """Keep the scheme://user part, replace only the password.

        中文：保留 scheme://user 部分，只替换密码。
        """
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
        # 中文：上面两种情况（env-assignment、dsn-password）的计数已经在各自的
        # 回调函数里通过 count 完成了，这里的 hits 不需要再用。
        del hits
    return text, count
