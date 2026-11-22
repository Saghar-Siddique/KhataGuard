"""Compatibility helpers for saving confirmed Part 4 drafts.

The helper supports both KhataGuard core snapshots seen in the provided files:
- record_sale(customer_name=...)
- record_sale(customer_id=...)
No existing core/database code is modified.
"""
from __future__ import annotations

import inspect
from datetime import date
from typing import Any, Callable


class IntegrationError(RuntimeError):
    pass


def _supports(function: Callable[..., Any], name: str) -> bool:
    return name in inspect.signature(function).parameters


def save_confirmed_transaction(
    *,
    customer_id: int,
    customer_name: str,
    sale: float,
    paid: float,
    transaction_date: date,
    description: str,
    record_sale: Callable[..., Any],
    record_payment: Callable[..., Any],
    allow_overpayment: bool = False,
) -> Any:
    """Save only after UI confirmation; never called during OCR/transcription."""
    sale = float(sale)
    paid = float(paid)
    if sale < 0 or paid < 0 or (sale == 0 and paid == 0):
        raise IntegrationError("Sale/payment amounts invalid hain.")
    if sale > 0 and paid > sale:
        raise IntegrationError("Paid new sale se zyada nahi ho sakta.")

    shared = {
        "description": description or None,
        "transaction_date": transaction_date.isoformat(),
    }

    if sale > 0:
        kwargs = {"sale_amount": sale, "paid_amount": paid, **shared}
        if _supports(record_sale, "customer_id"):
            kwargs["customer_id"] = customer_id
        elif _supports(record_sale, "customer_name"):
            kwargs["customer_name"] = customer_name
        else:
            raise IntegrationError("record_sale customer identifier support nahi karta.")
        return record_sale(**kwargs)

    kwargs = {"amount": paid, **shared}
    if allow_overpayment and _supports(record_payment, "allow_overpayment"):
        kwargs["allow_overpayment"] = True
    if _supports(record_payment, "customer_id"):
        kwargs["customer_id"] = customer_id
    elif _supports(record_payment, "customer_name"):
        kwargs["customer_name"] = customer_name
    else:
        raise IntegrationError("record_payment customer identifier support nahi karta.")
    return record_payment(**kwargs)


def save_confirmed_transactions(
    items,
    *,
    transaction_date: date,
    description: str,
    record_sale: Callable[..., Any],
    record_payment: Callable[..., Any],
    get_balance: Callable[[int], float] | None = None,
) -> list:
    """Save a confirmed batch in one go (Issue #2).

    items: list of dicts with customer_id, customer_name, sale, paid and
    optional allow_overpayment. The WHOLE batch is validated before anything
    is written, so one bad row cannot leave a half-saved batch. Overpayment
    is checked against the projected balance: the live balance plus earlier
    items in the batch (a sale earlier in the batch raises the balance a
    later payment can draw on). Returns the per-item save results, in order.
    """
    if not items:
        raise IntegrationError("Save karne ke liye koi transaction nahi hai.")

    # ---- validate the whole batch first (no writes yet) ----
    projected: dict[int, float] = {}
    for index, item in enumerate(items, start=1):
        label = f"Row {index} ({item.get('customer_name', '?')})"
        try:
            sale = float(item.get("sale", 0))
            paid = float(item.get("paid", 0))
        except (TypeError, ValueError):
            raise IntegrationError(f"{label}: amounts valid numbers hone chahiye.")
        if sale < 0 or paid < 0 or (sale == 0 and paid == 0):
            raise IntegrationError(f"{label}: sale/payment amounts invalid hain.")
        if sale > 0 and paid > sale:
            raise IntegrationError(f"{label}: paid new sale se zyada nahi ho sakta.")
        customer_id = item.get("customer_id")
        if customer_id is None:
            raise IntegrationError(f"{label}: customer select karein.")
        if get_balance is not None and sale == 0 and not item.get("allow_overpayment"):
            if customer_id not in projected:
                projected[customer_id] = float(get_balance(int(customer_id)))
            if paid > max(projected[customer_id], 0.0) + 0.005:
                raise IntegrationError(
                    f"{label}: payment Rs. {paid:,.2f} outstanding balance "
                    f"(Rs. {max(projected[customer_id], 0.0):,.2f}) se zyada hai. "
                    "Advance payment tick karein ya amount theek karein."
                )
        if customer_id not in projected and get_balance is not None:
            projected[customer_id] = float(get_balance(int(customer_id)))
        if customer_id in projected:
            projected[customer_id] += sale - paid

    # ---- all valid: save in order ----
    results = []
    for item in items:
        results.append(
            save_confirmed_transaction(
                customer_id=int(item["customer_id"]),
                customer_name=str(item["customer_name"]),
                sale=float(item.get("sale", 0)),
                paid=float(item.get("paid", 0)),
                transaction_date=transaction_date,
                description=description,
                record_sale=record_sale,
                record_payment=record_payment,
                allow_overpayment=bool(item.get("allow_overpayment", False)),
            )
        )
    return results
