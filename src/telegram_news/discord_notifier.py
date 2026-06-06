from __future__ import annotations

import html
import re
import time

import requests

DISCORD_MAX_CHARS = 2000


_LINK_RE = re.compile(r'<a\s+href="[^"]*"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")


def html_to_plain_text(text: str) -> str:
    """Convert the report's light HTML/Telegram link markup to Discord-safe plain text."""
    plain = text or ""
    plain = _LINK_RE.sub(lambda match: match.group(1), plain)
    plain = _TAG_RE.sub("", plain)
    plain = html.unescape(plain)
    plain = _BLANK_RE.sub("\n\n", plain)
    return plain.strip()


def split_for_discord(text: str, *, chunk_chars: int = DISCORD_MAX_CHARS) -> list[str]:
    """Split text into Discord webhook-safe chunks."""
    plain = html_to_plain_text(text)
    if not plain:
        return []

    limit = max(500, min(DISCORD_MAX_CHARS, int(chunk_chars)))
    chunks: list[str] = []
    current = ""

    for line in plain.split("\n"):
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line

    if current:
        chunks.append(current)
    return chunks


def send_discord_webhook(webhook_url: str, text: str, *, username: str | None = None) -> bool:
    """Send a report to Discord through an incoming webhook."""
    webhook_url = (webhook_url or "").strip()
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is required for Discord notification.")

    chunks = split_for_discord(text)
    if not chunks:
        return False

    total = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        prefix = f"({idx}/{total})\n" if total > 1 else ""
        payload: dict[str, str] = {"content": prefix + chunk}
        if username:
            payload["username"] = username
        response = requests.post(webhook_url, json=payload, timeout=20)
        if response.status_code not in (200, 204):
            raise RuntimeError(f"Discord webhook failed: HTTP {response.status_code}: {response.text[:500]}")
        if idx < total:
            time.sleep(0.5)
    return True
