# Unreleased (Issue #1 — audit log / undo)

- **Audit log** (`audit_log` table): every ledger write records actor, action and
  before/after snapshots. `initialize_database()` creates the table and migrates
  old databases (adds `voided` / `voided_at` to `transactions`).
- **Undo last entry** (`void_transaction`, `undo_last_transaction`, Ledger page
  button with confirm step): voids the latest entry — balance and totals are
  restored, the entry stays visible in the ledger marked void, never deleted.
  Voiding a sale+payment's payment only voids the payment; undo again for the sale.
- All balance/total paths ignore voided rows (`get_customer_balance`,
  `get_customer_ledger`, totals, outstanding customers, customer summaries).
- 13 new tests in `tests/test_audit_log.py` (42 total).

## Unreleased (Issue #2 — multiple transactions in one voice input)

- **`parse_transactions`** (`backend/ai/parser.py`): one Groq call extracts EVERY
  transaction in the input as a list (max 10). A single input still returns a
  one-item list. Any unclear item refuses the whole batch (clarification
  question) — a half-parsed batch is never saved. `parse_transaction` is
  unchanged for existing callers.
- **Batch confirm/save** (`backend/features/integration.py`,
  `backend/features/media.py`, Voice/Image page): the review screen shows one
  row per transaction with a single "Confirm & Save All (N)" button.
  `save_confirmed_transactions` validates the WHOLE batch first (amounts +
  overpayment against the projected balance, so a sale earlier in the batch
  funds a later payment) and only then saves — one bad row saves nothing.
- Parser eval is now 36 cases (20 extract + 4 multi + 12 clarify); `--dry`
  passes 36/36. Live accuracy re-run needs GROQ_API_KEY (not set here).
- 16 new tests in `tests/test_multi_transaction.py` (58 total).

# Changes in v1.0.0 (Oct 19, 2026)

## Data integrity
- **Duplicate customers blocked** (`add_customer`, `update_customer`): names compare case- and
  whitespace-insensitively. Existing duplicates in an old DB are not modified.
- **Overpayment guard** (`record_payment`): a payment above the outstanding balance is rejected
  unless `allow_overpayment=True`. UI: "Advance payment" checkbox on Transactions and Voice/Image.
- `KHATA_DB_PATH` env var lets you keep the real database outside the code folder.

## AI flow
- **Customer resolution** (`backend/features/matching.py`): voice/OCR names are matched against the
  ledger ("Ahmed" -> "Ahmed Khan", "Ahmad" -> "Ahmed"). If no exact match, the user must pick an existing
  customer or explicitly choose "Create NEW". No more silent duplicate customers.
- **Parser**: `reasoning_effort=low` + `include_reasoning=false` (auto-retry without if rejected),
  output cap 900 (< the 1,000 limit), tolerant JSON extraction, explicit empty/truncated-output errors.
- **Vision OCR**: non-thinking mode (`reasoning_effort=none`, `reasoning_format=hidden`, auto-retry
  without), `<think>` stripping, truncation error message.
- **Errors are no longer swallowed**: the parser's clarification question and HTTP status
  (401/429/...) now reach the user instead of a generic message.

## Quality
- `tests/`: 29 regression tests total (incl. 13 in `test_fixes.py` from this patch).
- `evals/run_parser_eval.py` + `parser_cases.jsonl`: 32 Urdu/Roman-Urdu/English cases
  (20 extraction, 12 must-clarify incl. a prompt-injection case). Run with `GROQ_API_KEY` set.

## Still NOT done (be aware)
- No authentication; SQLite on ephemeral hosts (Colab/Streamlit Cloud) loses data.
- One transaction per input (multi-entry page photos are rejected).
- No audit log / undo; no API cost guard.
- Urdu text in PDF statements needs a font with Arabic shaping (falls back to Helvetica).
