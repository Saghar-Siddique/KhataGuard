"""Tests for Issue #3: daily API cost guard (STT/TTS usage)."""
import io
import sqlite3
import sys
import tempfile
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ai import parser
from backend.database import database
from backend.features import cost_guard, media


@pytest.fixture
def db():
    tmp = tempfile.TemporaryDirectory()
    original = database.DATABASE_PATH
    database.DATABASE_PATH = Path(tmp.name) / "t.db"
    database.initialize_database()
    yield
    database.DATABASE_PATH = original
    tmp.cleanup()


def _connection():
    return sqlite3.connect(database.DATABASE_PATH)


def _wav(seconds: float, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


# ---------- settings / limit ----------

def test_default_daily_limit_is_50(db):
    assert cost_guard.get_daily_limit_pkr() == 50.0


def test_set_and_get_daily_limit(db):
    cost_guard.set_daily_limit_pkr(100.0)
    assert cost_guard.get_daily_limit_pkr() == 100.0


def test_set_daily_limit_rejects_non_positive(db):
    with pytest.raises(ValueError):
        cost_guard.set_daily_limit_pkr(0)
    with pytest.raises(ValueError):
        cost_guard.set_daily_limit_pkr(-5)


def test_invalid_stored_limit_falls_back_to_default(db):
    conn = _connection()
    conn.execute("INSERT INTO settings (key, value) VALUES ('daily_limit_pkr', 'nonsense')")
    conn.commit()
    conn.close()
    assert cost_guard.get_daily_limit_pkr() == 50.0


# ---------- usage log / cap ----------

def test_record_usage_and_today_spend(db):
    cost_guard.record_usage("stt", 10.5, "test")
    cost_guard.record_usage("parse", 5.25, "test")
    assert cost_guard.get_today_spend_pkr() == pytest.approx(15.75)


def test_record_usage_rejects_bad_kind_and_negative_cost(db):
    with pytest.raises(ValueError):
        cost_guard.record_usage("nope", 1.0)
    with pytest.raises(ValueError):
        cost_guard.record_usage("stt", -1.0)


def test_today_spend_ignores_yesterday(db):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connection()
    conn.execute(
        "INSERT INTO api_usage (kind, cost_pkr, detail, created_at) VALUES ('stt', 99.0, 'old', ?)",
        (yesterday,),
    )
    conn.commit()
    conn.close()
    cost_guard.record_usage("stt", 2.0, "today")
    assert cost_guard.get_today_spend_pkr() == pytest.approx(2.0)


def test_cap_hit_logic(db):
    cost_guard.set_daily_limit_pkr(50.0)
    assert not cost_guard.is_cap_hit()
    cost_guard.record_usage("stt", 49.0, "test")
    assert not cost_guard.is_cap_hit()
    cost_guard.record_usage("stt", 1.0, "test")
    assert cost_guard.is_cap_hit()


def test_ensure_under_cap_passes_when_under(db):
    cost_guard.set_daily_limit_pkr(50.0)
    cost_guard.ensure_under_cap("voice")  # must not raise


def test_ensure_under_cap_raises_and_logs_block(db):
    cost_guard.set_daily_limit_pkr(10.0)
    cost_guard.record_usage("stt", 10.0, "test")
    with pytest.raises(cost_guard.CostCapExceeded, match="Rs 10"):
        cost_guard.ensure_under_cap("voice")
    rows = cost_guard.get_recent_usage(limit=5)
    assert rows[0]["kind"] == "cap_blocked"
    assert rows[0]["cost_pkr"] == 0.0
    assert cost_guard.get_today_spend_pkr() == pytest.approx(10.0)


def test_recent_usage_newest_first(db):
    cost_guard.record_usage("stt", 1.0, "first")
    cost_guard.record_usage("parse", 2.0, "second")
    cost_guard.record_usage("ocr", 3.0, "third")
    rows = cost_guard.get_recent_usage(limit=2)
    assert [row["kind"] for row in rows] == ["ocr", "parse"]


# ---------- cost estimates ----------

def test_stt_cost_wav_exact_duration():
    # 30s WAV at $0.04/hr, Rs 280/$: 30/3600 * 0.04 * 280 = 0.0933
    cost, detail = cost_guard.estimate_stt_cost_pkr(
        _wav(30), "recording.wav", "whisper-large-v3-turbo"
    )
    assert cost == pytest.approx(0.0933, abs=1e-4)
    assert "exact" in detail


def test_stt_cost_applies_10s_minimum_billing():
    # 2s clip bills as 10s: 10/3600 * 0.04 * 280 = 0.0311
    cost, _ = cost_guard.estimate_stt_cost_pkr(
        _wav(2), "recording.wav", "whisper-large-v3-turbo"
    )
    assert cost == pytest.approx(0.0311, abs=1e-4)


def test_stt_cost_mp3_estimated_from_size():
    # 1,280,000 bytes at 128 kbps ≈ 80s: 80/3600 * 0.04 * 280 = 0.2489
    cost, detail = cost_guard.estimate_stt_cost_pkr(
        b"0" * 1_280_000, "recording.mp3", "whisper-large-v3-turbo"
    )
    assert cost == pytest.approx(0.2489, abs=1e-4)
    assert "estimated" in detail


def test_stt_cost_uses_v3_rate():
    # 3600s WAV at $0.111/hr: 1 * 0.111 * 280 = 31.08
    cost, _ = cost_guard.estimate_stt_cost_pkr(
        _wav(3600), "recording.wav", "whisper-large-v3"
    )
    assert cost == pytest.approx(31.08, abs=1e-2)


def test_chat_cost_estimate():
    # gpt-oss-20b, 1M in + 100k out: (0.075 + 0.03) * 280 = 29.4
    cost, detail = cost_guard.estimate_chat_cost_pkr(
        "openai/gpt-oss-20b", 1_000_000, 100_000
    )
    assert cost == pytest.approx(29.4, abs=1e-2)
    assert "gpt-oss-20b" in detail


# ---------- enforcement in media.py ----------

def _fake_post_ok(text="Ali ne 1000 ka saman liya", usage=None):
    class FakeResponse:
        status_code = 200

        def json(self):
            body = {"text": text, "choices": [{"message": {"content": text}}]}
            if usage is not None:
                body["usage"] = usage
            return body

    def fake_post(url, **kwargs):
        return FakeResponse()

    return fake_post


def test_transcribe_blocked_when_capped(db, monkeypatch):
    cost_guard.set_daily_limit_pkr(1.0)
    cost_guard.record_usage("stt", 1.0, "already spent")
    monkeypatch.setattr(
        media.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("API must not be called")),
    )
    with pytest.raises(cost_guard.CostCapExceeded):
        media.transcribe_audio(
            audio_bytes=_wav(30), filename="recording.wav", api_key="<redacted>"
        )
    rows = cost_guard.get_recent_usage(limit=1)
    assert rows[0]["kind"] == "cap_blocked"


def test_transcribe_records_stt_usage_on_success(db, monkeypatch):
    monkeypatch.setattr(media.requests, "post", _fake_post_ok())
    text = media.transcribe_audio(
        audio_bytes=_wav(30), filename="recording.wav", api_key="<redacted>"
    )
    assert text.startswith("Ali")
    assert cost_guard.get_today_spend_pkr() == pytest.approx(0.0933, abs=1e-4)
    rows = cost_guard.get_recent_usage(limit=1)
    assert rows[0]["kind"] == "stt"


def test_transcribe_failure_records_nothing(db, monkeypatch):
    class FakeResponse:
        status_code = 429
        text = ""

        def json(self):
            return {"error": {"message": "too many requests"}}

    monkeypatch.setattr(media.requests, "post", lambda *a, **k: FakeResponse())
    with pytest.raises(media.MediaProcessingError, match="429"):
        media.transcribe_audio(
            audio_bytes=_wav(30), filename="recording.wav", api_key="<redacted>"
        )
    assert cost_guard.get_recent_usage(limit=1) == []


def test_ocr_blocked_when_capped(db, monkeypatch):
    from PIL import Image

    cost_guard.set_daily_limit_pkr(1.0)
    cost_guard.record_usage("stt", 1.0, "already spent")
    monkeypatch.setattr(
        media.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("API must not be called")),
    )
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
    with pytest.raises(cost_guard.CostCapExceeded):
        media.ocr_handwritten_image(
            image_bytes=buf.getvalue(), filename="khata.png", api_key="<redacted>"
        )


def test_ocr_records_usage_from_reported_tokens(db, monkeypatch):
    from PIL import Image

    # qwen3.6-27b, 1000 in + 100 out: (1000*0.60 + 100*3.00)/1e6 * 280 = 0.252
    monkeypatch.setattr(
        media.requests,
        "post",
        _fake_post_ok(
            text="Ahmed 5000 sale, 2000 paid",
            usage={"prompt_tokens": 1000, "completion_tokens": 100},
        ),
    )
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
    text = media.ocr_handwritten_image(
        image_bytes=buf.getvalue(), filename="khata.png", api_key="<redacted>"
    )
    assert "Ahmed" in text
    assert cost_guard.get_today_spend_pkr() == pytest.approx(0.252, abs=1e-3)
    rows = cost_guard.get_recent_usage(limit=1)
    assert rows[0]["kind"] == "ocr"


def _fake_openai_module_with_usage(content, prompt_tokens, completion_tokens):
    class FakeMessage:
        def __init__(self):
            self.content = content

    class FakeChoice:
        def __init__(self):
            self.message = FakeMessage()
            self.finish_reason = "stop"

    class FakeResponse:
        def __init__(self):
            self.choices = [FakeChoice()]
            self.usage = SimpleNamespace(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
            )

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.chat = FakeChat()

    import types

    module = types.ModuleType("openai")
    module.OpenAI = FakeClient
    return module


def test_parse_usage_recorded_via_last_usage(db, monkeypatch):
    canned = (
        '{"transactions": [{"customer": "Ahmed", "sale": 5000, "paid": 2000, '
        '"needs_clarification": false, "question": ""}]}'
    )
    monkeypatch.setitem(
        sys.modules,
        "openai",
        _fake_openai_module_with_usage(canned, 2000, 200),
    )
    results = media.parse_many_with_existing_ai(
        text="Ahmed ne 5000 ka saman liya aur 2000 diye",
        parser=parser.parse_transactions,
        api_key="<redacted>",
        text_model="openai/gpt-oss-20b",
    )
    assert len(results) == 1
    # gpt-oss-20b, 2000 in + 200 out: (2000*0.075 + 200*0.30)/1e6 * 280 = 0.0588
    assert cost_guard.get_today_spend_pkr() == pytest.approx(0.0588, abs=1e-4)
    rows = cost_guard.get_recent_usage(limit=1)
    assert rows[0]["kind"] == "parse"
