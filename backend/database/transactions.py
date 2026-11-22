from .audit import insert_audit
from .database import get_connection


# ============================================================
# Allowed Transaction Types
# ============================================================

ALLOWED_TRANSACTION_TYPES = {"sale", "payment"}


# ============================================================
# Add Transaction
# ============================================================

def add_transaction(
    customer_id,
    transaction_type,
    amount,
    description=None,
    transaction_date=None,
    actor="owner"
):
    """
    Add a sale or payment transaction for a customer.

    sale    = amount customer owes
    payment = amount customer has paid

    The write is recorded in the audit log (Issue #1) with an
    after-snapshot of the new entry.
    """

    # Validate transaction type
    if transaction_type not in ALLOWED_TRANSACTION_TYPES:
        raise ValueError(
            "Transaction type must be 'sale' or 'payment'."
        )

    # Validate amount
    if amount is None:
        raise ValueError("Amount is required.")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a valid number.")

    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    connection = get_connection()

    try:
        cursor = connection.cursor()

        # Check that customer exists
        cursor.execute(
            """
            SELECT id
            FROM customers
            WHERE id = ?
            """,
            (customer_id,)
        )

        customer = cursor.fetchone()

        if customer is None:
            raise ValueError("Customer not found.")

        # Insert transaction
        if transaction_date:
            cursor.execute(
                """
                INSERT INTO transactions
                (
                    customer_id,
                    type,
                    amount,
                    description,
                    transaction_date
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    transaction_type,
                    amount,
                    description,
                    transaction_date
                )
            )
        else:
            cursor.execute(
                """
                INSERT INTO transactions
                (
                    customer_id,
                    type,
                    amount,
                    description
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    customer_id,
                    transaction_type,
                    amount,
                    description
                )
            )

        # One commit for the insert AND the audit row below, so a
        # failed audit write rolls the whole entry back.
        new_id = cursor.lastrowid

        # Audit log (Issue #1): record the new entry with an after-snapshot.
        # Same cursor/connection, so it commits together with the insert.
        insert_audit(
            cursor,
            actor=actor,
            action="add",
            transaction_id=new_id,
            customer_id=customer_id,
            after={
                "id": new_id,
                "customer_id": customer_id,
                "type": transaction_type,
                "amount": amount,
                "description": description,
                "transaction_date": transaction_date,
                "voided": 0,
            },
        )

        connection.commit()

        return new_id

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()



# ============================================================
# Get One Transaction
# ============================================================

def get_transaction(transaction_id):
    """
    Get one transaction by ID.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                t.id,
                t.customer_id,
                c.name AS customer_name,
                t.type,
                t.amount,
                t.description,
                t.transaction_date,
                t.created_at,
                t.voided,
                t.voided_at
            FROM transactions t
            JOIN customers c
                ON t.customer_id = c.id
            WHERE t.id = ?
            """,
            (transaction_id,)
        )

        return cursor.fetchone()

    finally:
        connection.close()


# ============================================================
# Get Customer Transactions
# ============================================================

def get_customer_transactions(customer_id):
    """
    Get all transactions belonging to one customer.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                customer_id,
                type,
                amount,
                description,
                transaction_date,
                created_at,
                voided,
                voided_at
            FROM transactions
            WHERE customer_id = ?
            ORDER BY transaction_date ASC, id ASC
            """,
            (customer_id,)
        )

        return cursor.fetchall()

    finally:
        connection.close()


# ============================================================
# Get All Transactions
# ============================================================

def get_all_transactions():
    """
    Get all transactions with customer names.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                t.id,
                t.customer_id,
                c.name AS customer_name,
                t.type,
                t.amount,
                t.description,
                t.transaction_date,
                t.created_at,
                t.voided,
                t.voided_at
            FROM transactions t
            JOIN customers c
                ON t.customer_id = c.id
            ORDER BY t.transaction_date DESC, t.id DESC
            """
        )

        return cursor.fetchall()

    finally:
        connection.close()


# ============================================================
# Delete Transaction
# ============================================================

def delete_transaction(transaction_id):
    """
    Delete a transaction by ID.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM transactions
            WHERE id = ?
            """,
            (transaction_id,)
        )

        if cursor.rowcount == 0:
            raise ValueError("Transaction not found.")

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

# ============================================================
# Void Transaction (Issue #1)
# ============================================================

def void_transaction(transaction_id, actor="owner"):
    """
    Mark a transaction void — never delete it.

    The entry stays in the ledger (visible, marked void) but no longer
    affects any balance or total. The audit log records the before/after
    snapshots, so the void itself is traceable.

    Returns a dict with the voided entry and the customer's balance
    before and after the void.
    """

    from .ledger import get_customer_balance

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                customer_id,
                type,
                amount,
                description,
                transaction_date,
                created_at,
                voided,
                voided_at
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,)
        )

        row = cursor.fetchone()

        if row is None:
            raise ValueError("Transaction not found.")

        if row["voided"]:
            raise ValueError("Transaction is already void.")

        before = dict(row)
        balance_before = get_customer_balance(row["customer_id"])

        cursor.execute(
            """
            UPDATE transactions
            SET voided = 1,
                voided_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (transaction_id,)
        )

        after = dict(before)
        after["voided"] = 1

        insert_audit(
            cursor,
            actor=actor,
            action="void",
            transaction_id=transaction_id,
            customer_id=row["customer_id"],
            before=before,
            after=after,
        )

        connection.commit()

        balance_after = get_customer_balance(row["customer_id"])

        return {
            "transaction_id": transaction_id,
            "customer_id": row["customer_id"],
            "type": row["type"],
            "amount": float(row["amount"]),
            "balance_before": balance_before,
            "balance_after": balance_after,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# ============================================================
# Undo Last Entry (Issue #1)
# ============================================================

def undo_last_transaction(actor="owner"):
    """
    Void the most recent non-void entry in the whole ledger.

    Use this for the dashboard "Undo last entry" action. It voids exactly
    one entry: if the last action was a sale with a part-payment, only the
    payment entry is voided (the sale stays) — undo again to void the sale.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM transactions
            WHERE voided = 0
            ORDER BY id DESC
            LIMIT 1
            """
        )

        row = cursor.fetchone()

        if row is None:
            raise ValueError("Nothing to undo.")

        transaction_id = row["id"]

    finally:
        connection.close()

    return void_transaction(transaction_id, actor=actor)

