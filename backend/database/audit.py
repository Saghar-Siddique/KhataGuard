"""Audit log (Issue #1).

Every ledger write is recorded with a before/after snapshot so a wrong
entry can be reviewed and undone. Nothing is ever deleted: undo marks
the entry void and the audit row is the permanent record of what happened.
"""
import json

from .database import get_connection


def insert_audit(cursor, *, actor, action, transaction_id, customer_id,
                 before=None, after=None, note=None):
    """Insert one audit row using an already-open cursor.

    The caller owns the transaction: the audit row commits (or rolls back)
    together with the ledger change it describes.
    """
    cursor.execute(
        """
        INSERT INTO audit_log
            (actor, action, transaction_id, customer_id,
             before_json, after_json, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor,
            action,
            transaction_id,
            customer_id,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
            note,
        ),
    )
    return cursor.lastrowid


def get_audit_log(limit=50):
    """Most recent audit entries, newest first."""
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, created_at, actor, action, transaction_id,
                   customer_id, before_json, after_json, note
            FROM audit_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

        return cursor.fetchall()

    finally:
        connection.close()


def get_audit_for_transaction(transaction_id):
    """Audit entries for one transaction, oldest first."""
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, created_at, actor, action, transaction_id,
                   customer_id, before_json, after_json, note
            FROM audit_log
            WHERE transaction_id = ?
            ORDER BY id ASC
            """,
            (transaction_id,),
        )

        return cursor.fetchall()

    finally:
        connection.close()
