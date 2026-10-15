"""Customer-name resolution for AI-generated drafts.

Voice/OCR gives names like "Ahmed" or "Ahmad Khan" while the ledger holds
"Ahmed Khan". Exact matching would silently create a duplicate customer, so
this module ranks existing customers and lets the UI ask the user to choose.

Pure Python (difflib), no database access, no network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

# Candidates scoring below this are not shown at all.
MIN_SCORE = 0.72


@dataclass(frozen=True)
class CustomerMatch:
    customer_id: int
    name: str
    score: float
    kind: str  # "exact" | "token" | "fuzzy"


def _clean(name: str) -> str:
    text = re.sub(r"[^\w\s]", " ", str(name or ""), flags=re.UNICODE)
    return " ".join(text.split()).casefold()


def _squash(token: str) -> str:
    """Light Roman-Urdu normalisation: collapse repeated letters, drop 'h' after vowels.

    'Ahmed' / 'Ahmad' / 'Ahmmed' end up close together.
    """
    token = re.sub(r"(.)\1+", r"\1", token)
    return token


def _token_score(a: str, b: str) -> float:
    return SequenceMatcher(None, _squash(a), _squash(b)).ratio()


def score_names(query: str, candidate: str) -> tuple[float, str]:
    """Return (score 0..1, kind) for a query name against one customer name."""
    q, c = _clean(query), _clean(candidate)
    if not q or not c:
        return 0.0, "fuzzy"
    if q == c:
        return 1.0, "exact"

    q_tokens, c_tokens = q.split(), c.split()

    # Every query token matches some candidate token ("Ahmed" -> "Ahmed Khan").
    if all(any(_token_score(qt, ct) >= 0.85 for ct in c_tokens) for qt in q_tokens):
        coverage = len(q_tokens) / max(len(c_tokens), 1)
        # Fewer unmatched candidate tokens -> slightly higher score, capped below exact.
        return round(0.80 + 0.15 * min(coverage, 1.0), 4), "token"

    whole = SequenceMatcher(None, _squash(q), _squash(c)).ratio()
    per_token = sum(
        max(_token_score(qt, ct) for ct in c_tokens) for qt in q_tokens
    ) / len(q_tokens)
    return round(max(whole, per_token * 0.95), 4), "fuzzy"


def rank_customers(
    name: str,
    customers: Iterable[Any],
    *,
    limit: int = 5,
    min_score: float = MIN_SCORE,
) -> list[CustomerMatch]:
    """Rank existing customers by similarity to `name` (best first)."""
    results: list[CustomerMatch] = []
    for row in customers:
        cid = int(row["id"])
        cname = str(row["name"])
        score, kind = score_names(name, cname)
        if score >= min_score:
            results.append(CustomerMatch(cid, cname, score, kind))
    results.sort(key=lambda m: (-m.score, m.name.casefold()))
    return results[:limit]


def exact_matches(name: str, customers: Iterable[Any]) -> list[CustomerMatch]:
    return [m for m in rank_customers(name, customers, limit=50) if m.kind == "exact"]
