"""Regression tests for Issue #1: audit log / undo for ledger entries."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import database
from backend.database.audit import get_audit_log, get_audit_for_transaction
from backend.database.ledger import (
    get_customer_balance,
    get_customer_ledger,
    get_total_sales,
    get_total_received,
    get_outstanding_customers,
)
from backend.database.transactions import (
    add_transaction,
    get_transaction,
    void_transaction,
    undo_last_transaction,
)
from backend.core import (
    create_customer,
    record_sale,
    record_payment,
    get_customer_summaries,
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


def _audit_rows():
    return [dict(row) for row in get_audit_log(limit=100)]


# ---------- schema / migration ----------

def test_audit_table_created_on_init(db):
    connection = sqlite3.connect(database.DATABASE_PATH)
    try:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "audit_log" in names
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(transactions)")
        }
        assert "voided" in columns
        assert "voided_at" in columns
    finally:
        connection.close()


def test_migration_upgrades_legacy_database():
    """An old DB (no voided columns, no audit_log) gains both on init."""
    tmp = tempfile.TemporaryDirectory()
    original = database.DATABASE_PATH
    try:
        database.DATABASE_PATH = Path(tmp.name) / "legacy.db"
        connection = sqlite3.connect(database.DATABASE_PATH)
        connection.execute(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                description TEXT,
                transaction_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO customers (name) VALUES ('Ahmed Khan')"
        )
        connection.execute(
            "INSERT INTO transactions (customer_id, type, amount) "
            "VALUES (1, 'sale', 5000)"
        )
        connection.commit()
        connection.close()

        database.initialize_database()

        # Old row survives, balances still correct, void works on it.
        assert get_customer_balance(1) == 5000
        result = void_transaction(1)
        assert result["balance_before"] == 5000
        assert result["balance_after"] == 0
        assert get_customer_balance(1) == 0
    finally:
        database.DATABASE_PATH = original
        tmp.cleanup()


# ---------- audit on add ----------

def test_add_transaction_writes_audit_entry(db):
    customer_id = create_customer("Ahmed Khan")
    tx_id = add_transaction(customer_id, "sale", 5000, "test sale")

    rows = _audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "add"
    assert row["transaction_id"] == tx_id
    assert row["customer_id"] == customer_id
    assert row["actor"] == "owner"
    after = json.loads(row["after_json"])
    assert after["amount"] == 5000
    assert after["type"] == "sale"
    assert after["voided"] == 0
    assert row["before_json"] is None


def test_record_sale_audits_both_entries(db):
    create_customer("Ahmed Khan")
    record_sale("Ahmed Khan", 5000, 2000, "shop sale")

    actions = [row["action"] for row in _audit_rows()]
    assert actions == ["add", "add"]


# ---------- void / undo ----------

def test_void_restores_balance_and_keeps_row(db):
    customer_id = create_customer("Ahmed Khan")
    tx_id = add_transaction(customer_id, "sale", 5000)

    assert get_customer_balance(customer_id) == 5000

    result = void_transaction(tx_id)
    assert result["balance_before"] == 5000
    assert result["balance_after"] == 0
    assert result["type"] == "sale"
    assert result["amount"] == 5000

    # Balance restored, row still there — marked void, never deleted.
    assert get_customer_balance(customer_id) == 0
    row = dict(get_transaction(tx_id))
    assert row["voided"] == 1
    assert row["voided_at"] is not None

    # Ledger still shows the entry, flagged void, balance untouched by it.
    ledger = get_customer_ledger(customer_id)
    assert len(ledger) == 1
    assert ledger[0]["voided"] == 1
    assert ledger[0]["debit"] == 0
    assert ledger[0]["balance"] == 0


def test_void_payment_restores_balance(db):
    customer_id = create_customer("Ahmed Khan")
    add_transaction(customer_id, "sale", 5000)
    payment_id = add_transaction(customer_id, "payment", 2000)

    assert get_customer_balance(customer_id) == 3000
    void_transaction(payment_id)
    assert get_customer_balance(customer_id) == 5000


def test_double_void_and_missing_void_rejected(db):
    customer_id = create_customer("Ahmed Khan")
    tx_id = add_transaction(customer_id, "sale", 5000)

    void_transaction(tx_id)
    with pytest.raises(ValueError, match="already void"):
        void_transaction(tx_id)
    with pytest.raises(ValueError, match="not found"):
        void_transaction(9999)


def test_undo_last_voids_latest_only(db):
    customer_id = create_customer("Ahmed Khan")
    add_transaction(customer_id, "sale", 1000)
    add_transaction(customer_id, "sale", 2000)

    assert get_customer_balance(customer_id) == 3000

    first = undo_last_transaction()
    assert first["amount"] == 2000
    assert first["balance_after"] == 1000

    second = undo_last_transaction()
    assert second["amount"] == 1000
    assert second["balance_after"] == 0

    with pytest.raises(ValueError, match="Nothing to undo"):
        undo_last_transaction()


def test_undo_after_sale_with_payment_voids_payment_only(db):
    create_customer("Ahmed Khan")
    record_sale("Ahmed Khan", 5000, 2000, "shop sale")

    result = undo_last_transaction()
    # The payment (latest entry) is voided; the sale stays.
    assert result["type"] == "payment"
    assert result["balance_after"] == 5000
    assert get_total_sales() == 5000
    assert get_total_received() == 0


def test_totals_and_summaries_exclude_voided(db):
    customer_id = create_customer("Ahmed Khan")
    other_id = create_customer("Sara Ali")
    sale_id = add_transaction(customer_id, "sale", 5000)
    add_transaction(customer_id, "payment", 2000)
    add_transaction(other_id, "sale", 3000)

    void_transaction(sale_id)

    assert get_total_sales() == 3000
    assert get_total_received() == 2000
    outstanding = [dict(row) for row in get_outstanding_customers()]
    assert [row["name"] for row in outstanding] == ["Sara Ali"]

    summaries = {row["name"]: row for row in get_customer_summaries()}
    assert summaries["Ahmed Khan"]["total_sale"] == 0
    assert summaries["Ahmed Khan"]["total_paid"] == 2000
    assert summaries["Ahmed Khan"]["outstanding"] == -2000
    assert summaries["Sara Ali"]["outstanding"] == 3000


def test_void_writes_audit_with_before_after_snapshots(db):
    customer_id = create_customer("Ahmed Khan")
    tx_id = add_transaction(customer_id, "sale", 5000)

    void_transaction(tx_id)

    rows = get_audit_for_transaction(tx_id)
    assert [row["action"] for row in rows] == ["add", "void"]
    void_row = dict(rows[1])
    before = json.loads(void_row["before_json"])
    after = json.loads(void_row["after_json"])
    assert before["voided"] == 0
    assert after["voided"] == 1
    assert after["amount"] == 5000


def test_audit_log_newest_first(db):
    customer_id = create_customer("Ahmed Khan")
    add_transaction(customer_id, "sale", 1000)
    add_transaction(customer_id, "sale", 2000)
    undo_last_transaction()

    actions = [row["action"] for row in get_audit_log(limit=10)]
    assert actions == ["void", "add", "add"]


def test_record_payment_still_guards_overpayment_after_void(db):
    create_customer("Ahmed Khan")
    record_sale("Ahmed Khan", 5000, 0, "shop sale")
    record_payment("Ahmed Khan", 5000, "paid in full")
    assert get_customer_balance(1) == 0
    # Any further payment is above the (zero) outstanding balance.
    with pytest.raises(ValueError, match="more than"):
        record_payment("Ahmed Khan", 100, "extra")
