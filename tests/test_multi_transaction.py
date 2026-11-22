"""Tests for Issue #2: multiple transactions in one voice input, batch confirm."""
import json
import sys
import tempfile
import types
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ai import parser
from backend.database import database
from backend.database.ledger import get_customer_balance
from backend.database.transactions import get_all_transactions
from backend.features import media
from backend.features.integration import (
    IntegrationError,
    save_confirmed_transactions,
)
from backend.core import (
    create_customer,
    record_payment,
    record_sale,
    get_customer_balance as core_balance,
)


@pytest.fixture
def db():
    tmp = tempfile.TemporaryDirectory()
    original = database.DATABASE_PATH
    database.DATABASE_PATH = Path(tmp.name) / "t.db"
    database.initialize_database()
    yield
    database.DATABASE_PATH = original
    tmp.cleanup()


# ---------- fake Groq client ----------

def _fake_openai_module(content, calls=None, finish_reason="stop"):
    """A fake `openai` module whose client returns `content` as the reply."""

    class FakeMessage:
        def __init__(self):
            self.content = content

    class FakeChoice:
        def __init__(self):
            self.message = FakeMessage()
            self.finish_reason = finish_reason

    class FakeResponse:
        def __init__(self):
            self.choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            return FakeResponse()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    module = types.ModuleType("openai")
    module.OpenAI = FakeOpenAI
    return module


def _wrapper(items):
    return json.dumps({
        "transactions": [
            {"customer": c, "sale": s, "paid": p,
             "needs_clarification": False, "question": ""}
            for c, s, p in items
        ]
    })


# ---------- parser: multi extraction ----------

def test_parse_transactions_three_items(monkeypatch):
    content = _wrapper([("Ali", 0, 500), ("Ahmed", 300, 0), ("Sana", 0, 800)])
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(content))
    out = parser.parse_transactions("Ali se 500 liye, Ahmed ko 300 diye, Sana ne 800 diye",
                                    api_key="k")
    assert [(d["customer"], d["sale"], d["paid"]) for d in out] == [
        ("Ali", 0, 500), ("Ahmed", 300, 0), ("Sana", 0, 800),
    ]


def test_parse_transactions_single_input_returns_one_item(monkeypatch):
    content = _wrapper([("Ahmed", 5000, 2000)])
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(content))
    out = parser.parse_transactions("Ahmed ne 5000 ka saman liya aur 2000 de diye", api_key="k")
    assert len(out) == 1 and out[0]["customer"] == "Ahmed" and out[0]["sale"] == 5000


def test_parse_transactions_accepts_bare_array_and_single_object(monkeypatch):
    bare_list = json.dumps([{"customer": "Ali", "sale": 0, "paid": 500,
                             "needs_clarification": False, "question": ""}])
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(bare_list))
    assert len(parser.parse_transactions("x", api_key="k")) == 1

    single = json.dumps({"customer": "Ali", "sale": 0, "paid": 500,
                         "needs_clarification": False, "question": ""})
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(single))
    assert len(parser.parse_transactions("x", api_key="k")) == 1


def test_parse_transactions_clarification_refuses_whole_batch(monkeypatch):
    content = json.dumps({"needs_clarification": True,
                          "question": "Kis customer ke kitne paise hain?",
                          "transactions": []})
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(content))
    with pytest.raises(ValueError, match="Kis customer"):
        parser.parse_transactions("Ahmed aur Bilal ne 2000 diye", api_key="k")


def test_parse_transactions_invalid_item_rejected(monkeypatch):
    bad = _wrapper([("Ali", 0, 500), ("Ahmed", -300, 0)])
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(bad))
    with pytest.raises(ValueError, match="negative"):
        parser.parse_transactions("x", api_key="k")

    overpaid = _wrapper([("Ahmed", 100, 200)])
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(overpaid))
    with pytest.raises(ValueError, match="greater than sale"):
        parser.parse_transactions("x", api_key="k")


def test_parse_transactions_too_many_rejected(monkeypatch):
    many = _wrapper([(f"C{i}", 100, 0) for i in range(11)])
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(many))
    with pytest.raises(ValueError, match="Too many"):
        parser.parse_transactions("x", api_key="k")


def test_parse_transactions_empty_and_missing_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="empty"):
        parser.parse_transactions("   ", api_key="k")
    with pytest.raises(ValueError, match="not configured"):
        parser.parse_transactions("Ahmed 5000")


def test_parse_transactions_prompt_and_token_cap(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(_wrapper([("Ali", 0, 500)]), calls))
    parser.parse_transactions("x", api_key="k")
    assert len(calls) == 1
    request = calls[0]
    assert request["max_completion_tokens"] == 900
    assert request["response_format"] == {"type": "json_object"}
    system = request["messages"][0]["content"]
    assert "transactions" in system and "EVERY" in system


def test_parse_transaction_single_path_unchanged(monkeypatch):
    """The old single-transaction function still works after the refactor."""
    content = json.dumps({"customer": "Ahmed", "sale": 5000, "paid": 2000,
                          "needs_clarification": False, "question": ""})
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(content))
    out = parser.parse_transaction("Ahmed ne 5000 ka saman liya aur 2000 de diye", api_key="k")
    assert out == {"customer": "Ahmed", "sale": 5000.0, "paid": 2000.0,
                   "needs_clarification": False, "question": ""}


# ---------- media: batch normalize ----------

def test_media_parse_many_returns_list():
    def fake_parser(text, api_key=None, model=None):
        return [
            {"customer": "Ali", "sale": 0, "paid": 500,
             "needs_clarification": False, "question": ""},
            {"customer": "Ahmed", "sale": 300, "paid": 0,
             "needs_clarification": False, "question": ""},
        ]

    out = media.parse_many_with_existing_ai(text="x", parser=fake_parser,
                                            api_key="k", text_model="m")
    assert [(t.customer, t.sale, t.paid) for t in out] == [
        ("Ali", 0, 500), ("Ahmed", 300, 0),
    ]


def test_media_parse_many_single_dict_wraps_to_one_item():
    def fake_parser(text, api_key=None, model=None):
        return {"customer": "Ali", "sale": 0, "paid": 500,
                "needs_clarification": False, "question": ""}

    out = media.parse_many_with_existing_ai(text="x", parser=fake_parser,
                                            api_key="k", text_model="m")
    assert len(out) == 1 and out[0].customer == "Ali"


def test_media_parse_many_clarification_reaches_user():
    def fake_parser(text, api_key=None, model=None):
        raise ValueError("Kis customer ke kitne paise hain?")

    with pytest.raises(media.MediaProcessingError, match="Kis customer"):
        media.parse_many_with_existing_ai(text="x", parser=fake_parser,
                                          api_key="k", text_model="m")


# ---------- batch save ----------

def _item(customer_id, name, sale, paid, advance=False):
    return {"customer_id": customer_id, "customer_name": name,
            "sale": sale, "paid": paid, "allow_overpayment": advance}


def test_batch_save_three_entries_after_single_confirmation(db):
    ali = create_customer("Ali")
    ahmed = create_customer("Ahmed")
    sana = create_customer("Sana")
    record_sale("Ali", 1000, 0, "seed")
    record_sale("Sana", 1000, 0, "seed")

    results = save_confirmed_transactions(
        [_item(ali, "Ali", 0, 500),
         _item(ahmed, "Ahmed", 300, 0),
         _item(sana, "Sana", 0, 800)],
        transaction_date=date.today(),
        description="voice batch",
        record_sale=record_sale,
        record_payment=record_payment,
        get_balance=core_balance,
    )
    assert len(results) == 3
    assert get_customer_balance(ali) == 500
    assert get_customer_balance(ahmed) == 300
    assert get_customer_balance(sana) == 200
    assert len(get_all_transactions()) == 5  # 2 seed + 3 batch


def test_batch_save_atomic_on_bad_row(db):
    ali = create_customer("Ali")
    ahmed = create_customer("Ahmed")
    record_sale("Ali", 1000, 0, "seed")

    with pytest.raises(IntegrationError, match="Row 3"):
        save_confirmed_transactions(
            [_item(ali, "Ali", 0, 500),
             _item(ahmed, "Ahmed", 300, 0),
             _item(ali, "Ali", 0, 5000)],  # overpayment, no advance
            transaction_date=date.today(),
            description="voice batch",
            record_sale=record_sale,
            record_payment=record_payment,
            get_balance=core_balance,
        )
    # Nothing from the batch was saved.
    assert len(get_all_transactions()) == 1
    assert get_customer_balance(ali) == 1000
    assert get_customer_balance(ahmed) == 0


def test_batch_save_projected_balance_allows_later_payment(db):
    ahmed = create_customer("Ahmed")
    # Live balance is 0, but the sale earlier in the SAME batch funds the payment.
    results = save_confirmed_transactions(
        [_item(ahmed, "Ahmed", 1000, 0),
         _item(ahmed, "Ahmed", 0, 1000)],
        transaction_date=date.today(),
        description="voice batch",
        record_sale=record_sale,
        record_payment=record_payment,
        get_balance=core_balance,
    )
    assert len(results) == 2
    assert get_customer_balance(ahmed) == 0


def test_batch_save_advance_payment_allowed_when_flagged(db):
    ahmed = create_customer("Ahmed")
    results = save_confirmed_transactions(
        [_item(ahmed, "Ahmed", 0, 500, advance=True)],
        transaction_date=date.today(),
        description="advance",
        record_sale=record_sale,
        record_payment=record_payment,
        get_balance=core_balance,
    )
    assert len(results) == 1
    assert get_customer_balance(ahmed) == -500
