"""Regression tests for: duplicate customers, overpayment guard, fuzzy matching,
parser error handling."""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import database
from backend.core import (
    create_customer, find_customer_by_name, record_sale, record_payment, list_customers,
)
from backend.database.customers import update_customer
from backend.features.matching import rank_customers, score_names
from backend.features.integration import save_confirmed_transaction
from backend.features import media
from backend.ai import parser


@pytest.fixture
def db():
    tmp = tempfile.TemporaryDirectory()
    original = database.DATABASE_PATH
    database.DATABASE_PATH = Path(tmp.name) / "t.db"
    database.initialize_database()
    yield
    database.DATABASE_PATH = original
    tmp.cleanup()


# ---------- duplicate customers ----------
def test_duplicate_name_rejected_case_and_space_insensitive(db):
    create_customer("Ahmed Khan")
    for dup in ("ahmed khan", "  AHMED   KHAN ", "Ahmed Khan"):
        with pytest.raises(ValueError, match="already exists"):
            create_customer(dup)
    assert len(list_customers()) == 1


def test_update_customer_cannot_collide_but_can_keep_own_name(db):
    a = create_customer("Ali")
    create_customer("Bilal")
    with pytest.raises(ValueError, match="already exists"):
        update_customer(a, "bilal")
    update_customer(a, "ALI", "0300")  # same customer, case change only
    assert find_customer_by_name("ali")["phone"] == "0300"


def test_find_customer_ignores_inner_whitespace(db):
    create_customer("Ahmed Khan")
    assert find_customer_by_name("ahmed   khan")["name"] == "Ahmed Khan"


# ---------- overpayment ----------
def test_payment_above_outstanding_is_rejected(db):
    create_customer("Ahmed")
    record_sale("Ahmed", 5000, 2000)           # outstanding 3000
    with pytest.raises(ValueError, match="outstanding"):
        record_payment("Ahmed", 999999)
    assert record_payment("Ahmed", 3000)["outstanding"] == 0   # exact settle OK


def test_payment_with_no_balance_rejected_unless_advance(db):
    create_customer("Ahmed")
    with pytest.raises(ValueError):
        record_payment("Ahmed", 100)
    r = record_payment("Ahmed", 100, allow_overpayment=True)
    assert r["outstanding"] == -100


def test_integration_forwards_advance_flag(db):
    cid = create_customer("Ahmed")
    from datetime import date
    kw = dict(customer_id=cid, customer_name="Ahmed", sale=0, paid=500,
              transaction_date=date.today(), description="x",
              record_sale=record_sale, record_payment=record_payment)
    with pytest.raises(ValueError):
        save_confirmed_transaction(**kw)
    assert save_confirmed_transaction(**kw, allow_overpayment=True)["outstanding"] == -500


# ---------- fuzzy matching ----------
CUSTOMERS = [
    {"id": 1, "name": "Ahmed Khan"}, {"id": 2, "name": "Bilal Ahmed"},
    {"id": 3, "name": "Kashif Ali"}, {"id": 4, "name": "Tariq Mehmood"},
]


def test_first_name_matches_full_name():
    top = rank_customers("Ahmed", CUSTOMERS)
    assert top[0].name == "Ahmed Khan" and top[0].kind == "token"


def test_spelling_variants_match():
    assert rank_customers("Ahmad Khan", CUSTOMERS)[0].customer_id == 1
    assert rank_customers("Mehmud", CUSTOMERS)[0].customer_id == 4


def test_exact_is_flagged_and_unrelated_returns_nothing():
    assert rank_customers("kashif ali", CUSTOMERS)[0].kind == "exact"
    assert rank_customers("Zainab Fatima", CUSTOMERS) == []
    assert score_names("", "x")[0] == 0.0


# ---------- parser / media error handling ----------
def test_parser_extracts_json_from_fenced_text():
    assert parser._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parser._extract_json('Sure! {"a": 2} done') == {"a": 2}
    with pytest.raises(ValueError, match="empty"):
        parser._extract_json("")
    with pytest.raises(ValueError, match="valid JSON"):
        parser._extract_json("no json here")


def test_clarification_question_reaches_user():
    def fake_parser(text, api_key=None, model=None):
        return parser._validate_result(
            {"needs_clarification": True, "question": "Kis customer ka payment hai?"})
    with pytest.raises(media.MediaProcessingError, match="Kis customer"):
        media.parse_with_existing_ai(text="payment mili", parser=fake_parser,
                                     api_key="k", text_model="m")


def test_api_status_error_is_not_swallowed():
    class Boom(Exception):
        status_code = 429
    def fake_parser(text, api_key=None, model=None):
        raise Boom("rate limited")
    with pytest.raises(media.MediaProcessingError, match="429"):
        media.parse_with_existing_ai(text="x", parser=fake_parser, api_key="k", text_model="m")


def test_vision_retries_without_reasoning_params_and_strips_think(monkeypatch):
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (4, 4)).save(buf, "PNG")
    calls = []

    class R:
        def __init__(self, status, body): self.status_code, self._b, self.text = status, body, ""
        def json(self): return self._b

    def fake_post(url, **kw):
        calls.append(kw["json"])
        if "reasoning_effort" in kw["json"]:
            return R(400, {"error": {"message": "reasoning_effort not supported"}})
        return R(200, {"choices": [{"message": {"content": "<think>hmm</think>Ali 500"}}]})

    monkeypatch.setattr(media.requests, "post", fake_post)
    out = media.ocr_handwritten_image(image_bytes=buf.getvalue(), filename="a.png", api_key="k")
    assert out == "Ali 500"
    assert len(calls) == 2 and "reasoning_effort" not in calls[1]
    assert all(c["max_completion_tokens"] < 1000 for c in calls)
