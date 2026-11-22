"""Issue #3: daily API cost guard for voice (STT) and media AI usage.

Why this exists: every voice note hits paid Groq APIs with no cap. This
module tracks estimated daily spend, enforces a daily limit (default
Rs 50/day, changeable in Settings), blocks further voice/media processing
once the cap is hit, and logs the block event.

What it costs (Groq published rates, checked Oct 2026):
- Whisper large-v3-turbo: $0.04 per audio hour (10-second minimum billing
  per request); Whisper large-v3: $0.111 per audio hour.
- gpt-oss-20b chat: $0.075 / $0.30 per 1M input / output tokens.
- gpt-oss-120b chat: $0.15 / $0.60 per 1M tokens.
- qwen3.6-27b (vision OCR): $0.60 / $3.00 per 1M tokens.

USD_TO_PKR is an assumed rate for estimates. Every cost here is an
ESTIMATE for the safety rail, never a bill. Groq's free tier currently
bills $0, so real spend stays far below the cap in normal use.

What is NOT covered: TTS does not exist in the app yet. When a TTS call
is added, it should call ensure_under_cap() before the request and
record_usage("tts", ...) after it, using the same helpers.

Days are counted in Pakistan time (Asia/Karachi, UTC+5, no DST): the
limit resets at PKT midnight.
"""
from __future__ import annotations

import io
import sqlite3
import wave
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from backend.database.database import get_connection

# ---------------------------------------------------------------
# Rates and defaults (see module docstring for sources)
# ---------------------------------------------------------------

DEFAULT_DAILY_LIMIT_PKR = 50.0
USD_TO_PKR = 280.0  # assumed rate, Nov 2026; estimates only

WHISPER_USD_PER_AUDIO_HOUR = {
    "whisper-large-v3-turbo": 0.04,
    "whisper-large-v3": 0.111,
}
WHISPER_MIN_BILLABLE_SECONDS = 10  # Groq bills a minimum of 10s per request
WHISPER_FALLBACK_USD_PER_AUDIO_HOUR = 0.04  # unknown model: cheapest rate

# (input USD per 1M tokens, output USD per 1M tokens)
CHAT_USD_PER_M_TOKENS = {
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "qwen/qwen3.6-27b": (0.60, 3.00),
}
CHAT_FALLBACK_USD_PER_M_TOKENS = (0.075, 0.30)  # unknown model: gpt-oss-20b rates

# Bitrate assumed when the audio format has no readable duration
# (MP3/M4A). WAV duration is read exactly; this is only a fallback.
ASSUMED_AUDIO_KBPS = 128

PKT = timezone(timedelta(hours=5))
USAGE_KINDS = ("stt", "parse", "ocr", "cap_blocked")


class CostCapExceeded(RuntimeError):
    """Raised when today's estimated spend has hit the daily limit."""


# ---------------------------------------------------------------
# Settings (daily limit)
# ---------------------------------------------------------------

def get_daily_limit_pkr(connection: sqlite3.Connection | None = None) -> float:
    """Daily spend limit in Rs. Defaults to 50.0 when unset or invalid."""
    conn = connection or get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'daily_limit_pkr'"
        ).fetchone()
        if row is None:
            return DEFAULT_DAILY_LIMIT_PKR
        try:
            limit = float(row[0])
        except (TypeError, ValueError):
            return DEFAULT_DAILY_LIMIT_PKR
        return limit if limit > 0 else DEFAULT_DAILY_LIMIT_PKR
    finally:
        if connection is None:
            conn.close()


def set_daily_limit_pkr(limit: float, connection: sqlite3.Connection | None = None) -> float:
    """Save a new daily limit in Rs. Must be greater than zero."""
    limit = float(limit)
    if limit <= 0:
        raise ValueError("Daily limit must be greater than zero.")
    conn = connection or get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('daily_limit_pkr', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (str(limit),),
        )
        conn.commit()
    finally:
        if connection is None:
            conn.close()
    return limit


# ---------------------------------------------------------------
# Usage log
# ---------------------------------------------------------------

def record_usage(
    kind: str,
    cost_pkr: float,
    detail: str = "",
    connection: sqlite3.Connection | None = None,
) -> int:
    """Append one usage row. Returns the new row id."""
    if kind not in USAGE_KINDS:
        raise ValueError(f"Unknown usage kind: {kind!r}.")
    cost = float(cost_pkr)
    if cost < 0:
        raise ValueError("Usage cost cannot be negative.")
    conn = connection or get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO api_usage (kind, cost_pkr, detail) VALUES (?, ?, ?)",
            (kind, cost, detail),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        if connection is None:
            conn.close()


def _pakistan_today() -> date:
    return datetime.now(PKT).date()


def _row_day(created_at: str) -> date | None:
    """created_at is stored in UTC; return its calendar day in Pakistan."""
    try:
        moment = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=timezone.utc).astimezone(PKT).date()


def get_today_spend_pkr(
    day: date | None = None,
    connection: sqlite3.Connection | None = None,
) -> float:
    """Estimated spend in Rs for the given day (default: today, Pakistan)."""
    target = day or _pakistan_today()
    conn = connection or get_connection()
    try:
        rows = conn.execute("SELECT cost_pkr, created_at FROM api_usage").fetchall()
    finally:
        if connection is None:
            conn.close()
    total = 0.0
    for cost, created_at in rows:
        if _row_day(created_at) == target:
            total += float(cost or 0)
    return total


def get_recent_usage(
    limit: int = 20,
    connection: sqlite3.Connection | None = None,
) -> list[dict]:
    """Newest usage rows first (for the Settings page log)."""
    conn = connection or get_connection()
    try:
        rows = conn.execute(
            "SELECT created_at, kind, cost_pkr, detail FROM api_usage "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        if connection is None:
            conn.close()
    return [
        {
            "created_at": row[0],
            "kind": row[1],
            "cost_pkr": float(row[2] or 0),
            "detail": row[3] or "",
        }
        for row in rows
    ]


def is_cap_hit(
    day: date | None = None,
    connection: sqlite3.Connection | None = None,
) -> bool:
    """True when today's estimated spend has reached the daily limit."""
    return get_today_spend_pkr(day=day, connection=connection) >= get_daily_limit_pkr(
        connection=connection
    )


def ensure_under_cap(
    what: str = "voice",
    day: date | None = None,
    connection: sqlite3.Connection | None = None,
) -> None:
    """Block a paid call when the cap is hit.

    Logs the block event (kind "cap_blocked", cost 0) and raises
    CostCapExceeded. Call this immediately before the API request, so a
    blocked attempt never spends anything. Display-only checks should use
    is_cap_hit() instead, which logs nothing.
    """
    limit = get_daily_limit_pkr(connection=connection)
    spend = get_today_spend_pkr(day=day, connection=connection)
    if spend < limit:
        return
    record_usage(
        "cap_blocked",
        0.0,
        f"Blocked {what} at Rs {spend:.2f} / Rs {limit:.2f} daily limit.",
        connection=connection,
    )
    raise CostCapExceeded(
        f"Aaj ki Rs {limit:.0f} daily AI limit khatam ho gayi hai "
        f"(spend Rs {spend:.2f}). {what.capitalize()} band hai — text se entry karein."
    )


# ---------------------------------------------------------------
# Cost estimates
# ---------------------------------------------------------------

def audio_duration_seconds(audio_bytes: bytes, filename: str) -> tuple[float, str]:
    """Audio length in seconds. WAV is read exactly; MP3/M4A is estimated
    from file size at the assumed bitrate. Returns (seconds, how)."""
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".wav":
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                if rate > 0:
                    return frames / rate, "exact"
        except (wave.Error, EOFError, ValueError):
            pass
    seconds = len(audio_bytes) * 8 / (ASSUMED_AUDIO_KBPS * 1000)
    return seconds, "estimated"


def estimate_stt_cost_pkr(
    audio_bytes: bytes,
    filename: str,
    model: str,
) -> tuple[float, str]:
    """Estimated STT cost in Rs for one transcription, plus a detail line."""
    seconds, how = audio_duration_seconds(audio_bytes, filename)
    billable = max(seconds, WHISPER_MIN_BILLABLE_SECONDS)
    rate = WHISPER_USD_PER_AUDIO_HOUR.get(model, WHISPER_FALLBACK_USD_PER_AUDIO_HOUR)
    cost = billable / 3600 * rate * USD_TO_PKR
    note = "" if model in WHISPER_USD_PER_AUDIO_HOUR else " (unknown model, turbo rate used)"
    detail = (
        f"{model}, {seconds:.1f}s audio ({how}), "
        f"billed {billable:.0f}s at ${rate}/hr{note}"
    )
    return round(cost, 4), detail


def estimate_chat_cost_pkr(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> tuple[float, str]:
    """Estimated chat-completion cost in Rs from token usage, plus detail."""
    rate_in, rate_out = CHAT_USD_PER_M_TOKENS.get(model, CHAT_FALLBACK_USD_PER_M_TOKENS)
    cost = (prompt_tokens * rate_in + completion_tokens * rate_out) / 1_000_000 * USD_TO_PKR
    note = "" if model in CHAT_USD_PER_M_TOKENS else " (unknown model, gpt-oss-20b rates used)"
    detail = (
        f"{model}, {prompt_tokens} in + {completion_tokens} out tokens{note}"
    )
    return round(cost, 4), detail
