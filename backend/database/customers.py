from .database import get_connection


def normalize_name(name):
    """Collapse whitespace so 'Ahmed   Khan ' and 'ahmed khan' compare equal."""
    return " ".join(str(name).split())


def _name_key(name):
    return normalize_name(name).casefold()


def _assert_name_available(cursor, name, exclude_id=None):
    """Raise ValueError if another customer already has this name."""
    key = _name_key(name)
    cursor.execute("SELECT id, name FROM customers")
    for row in cursor.fetchall():
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        if _name_key(row["name"]) == key:
            raise ValueError(
                f"Customer '{row['name']}' already exists. "
                "Use a different name (e.g. add father's name or area)."
            )


def add_customer(name, phone=None):
    """Add a new customer and return the customer ID."""

    if not name or not name.strip():
        raise ValueError("Customer name cannot be empty.")

    name = normalize_name(name)

    if phone is not None:
        phone = phone.strip()

    connection = get_connection()

    try:
        cursor = connection.cursor()

        _assert_name_available(cursor, name)

        cursor.execute(
            """
            INSERT INTO customers (name, phone)
            VALUES (?, ?)
            """,
            (name, phone)
        )

        connection.commit()

        return cursor.lastrowid

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_customer(customer_id):
    """Get one customer by ID."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, name, phone, created_at
            FROM customers
            WHERE id = ?
            """,
            (customer_id,)
        )

        return cursor.fetchone()

    finally:
        connection.close()


def get_all_customers():
    """Get all customers."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, name, phone, created_at
            FROM customers
            ORDER BY name ASC
            """
        )

        return cursor.fetchall()

    finally:
        connection.close()


def update_customer(customer_id, name, phone=None):
    """Update an existing customer."""

    if not name or not name.strip():
        raise ValueError("Customer name cannot be empty.")

    name = normalize_name(name)

    if phone is not None:
        phone = phone.strip()

    connection = get_connection()

    try:
        cursor = connection.cursor()

        _assert_name_available(cursor, name, exclude_id=customer_id)

        cursor.execute(
            """
            UPDATE customers
            SET name = ?, phone = ?
            WHERE id = ?
            """,
            (name, phone, customer_id)
        )

        if cursor.rowcount == 0:
            raise ValueError("Customer not found.")

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def delete_customer(customer_id):
    """Delete a customer if they have no transactions."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM customers
            WHERE id = ?
            """,
            (customer_id,)
        )

        if cursor.rowcount == 0:
            raise ValueError("Customer not found.")

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
