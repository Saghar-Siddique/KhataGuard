# Changes in this version

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
- `tests/test_fixes.py`: 13 regression tests.
- `evals/run_parser_eval.py` + `parser_cases.jsonl`: 32 Urdu/Roman-Urdu/English cases
  (20 extraction, 12 must-clarify incl. a prompt-injection case). Run with `GROQ_API_KEY` set.

## Still NOT done (be aware)
- No authentication; SQLite on ephemeral hosts (Colab/Streamlit Cloud) loses data.
- One transaction per input (multi-entry page photos are rejected).
- No audit log / undo; no API cost guard.
- Urdu text in PDF statements needs a font with Arabic shaping (falls back to Helvetica).
