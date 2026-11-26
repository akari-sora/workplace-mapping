"""Recognize common credential forms in config and generated output."""

import re
import tomllib
from collections.abc import Iterable
from typing import Any

from .config import Config


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"),
    re.compile(r"otpauth://[^\s<>\"']+", re.IGNORECASE),
    re.compile(r"(?<![\w-])(?:ghp_|github_pat_|glpat-|sk-|xoxb-|xoxp-)[A-Za-z0-9_-]+"),
    re.compile(r"(?<!\w)AKIA[A-Z0-9]+"),
    re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
    re.compile(
        r"""(?<![\w-])(?:password|passwd|secret|token|api_key)["']?\s*[:=]\s*"""
        r"""(?:"[^"\r\n]{8,}"|'[^'\r\n]{8,}'|\x60[^\x60\r\n]{8,}\x60|[^\s"'\x60,;{}\[\]]{8,})""",
        re.IGNORECASE,
    ),
)


PRIVATE_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.DOTALL,
)


def has_secret_like(text: str) -> bool:
    return PRIVATE_BLOCK.search(text) is not None or any(pattern.search(text) for pattern in SECRET_PATTERNS)


def redact_output(config: Config, output: Any, *, private: Iterable[str] = ()) -> Any:
    lines = []
    payloads = sorted({text for text in private if text.strip()}, key=len, reverse=True)
    literals = set()
    try:
        rules = tomllib.loads(config.path("rules").read_text(encoding="utf-8")).get("outbound", [])
    except (OSError, ValueError):
        rules = []
    sources = [(config.registrations[kind], key) for kind, key in (
        ("system", "probe"), ("channel", "send"), ("command", "run"),
        ("channel", "readback"), ("command", "readback"),
    )]
    sources.append((rules, "check"))
    for entries, key in sources:
        for entry in entries if isinstance(entries, list) else [entries]:
            value = entry.get(key) if isinstance(entry, dict) else None
            if isinstance(value, str) and value.strip():
                lines.append(value)
                for match in SECRET_PATTERNS[-1].finditer(value):
                    literal = re.split(r"[:=]", match[0], maxsplit=1)[1].strip().strip("\"'`")
                    literals.add(literal)

    ordered_lines = sorted(set(lines), key=len, reverse=True)

    def redact(text: str, *, include_payloads: bool) -> str:
        if include_payloads:
            for payload in payloads:
                if text == payload:
                    text = "[redacted]"
                elif len(payload) > 1 and payload in text:
                    if len(payload) > 120:
                        text = text.replace(payload, "[redacted]")
                        continue
                    start = r"(?<!\w)" if payload[0].isalnum() else ""
                    end = r"(?!\w)" if payload[-1].isalnum() else ""
                    text = re.sub(start + re.escape(payload) + end, "[redacted]", text)
        for line in ordered_lines:
            if text == line or (len(line) > 1 and line in text):
                text = text.replace(line, "[redacted]")
        for literal in sorted(literals, key=len, reverse=True):
            text = text.replace(literal, "[redacted]")
        text = PRIVATE_BLOCK.sub("[redacted]", text)
        for pattern in SECRET_PATTERNS:
            text = pattern.sub("[redacted]", text)
        return text

    def clean(value: Any, key: str = "") -> Any:
        if isinstance(value, str):
            if key == "payload":
                return value
            if key in {"status", "level", "code"} and re.fullmatch(r"[a-z_]+", value):
                return value
            return redact(value, include_payloads=key not in {
                "payload", "id", "state", "status", "level", "code", "where", "next", "kind", "check", "result_label",
            })
        if isinstance(value, list):
            return [clean(item, key) for item in value]
        if isinstance(value, dict):
            return {field: clean(item, field if field != "result" or "id" in value else "result_label")
                    for field, item in value.items()}
        return value

    return clean(output)
