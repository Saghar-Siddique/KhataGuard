"""Parser accuracy eval for KhataGuard (Urdu / Roman Urdu / English).

  python evals/run_parser_eval.py            # live: needs GROQ_API_KEY
  python evals/run_parser_eval.py --dry      # offline: checks dataset + scorer only

Scoring per case:
  expected dict   -> pass if the parser returns that one transaction
  expected list   -> pass if the parser returns exactly those transactions, in order
  expected "clarify" -> pass only if the parser refuses (raises ValueError)
Reports overall accuracy, per-bucket accuracy, and every failure.
"""
import argparse, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.ai import parser  # noqa: E402


def load(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _expected_list(expected):
    """Normalize an expected value to a list of transactions (or None for clarify)."""
    if expected == "clarify":
        return None
    if isinstance(expected, dict):
        return [expected]
    return expected


def _items_match(got, exp):
    return (
        got["customer"].strip().casefold() == exp["customer"].casefold()
        and abs(got["sale"] - exp["sale"]) < 0.01
        and abs(got["paid"] - exp["paid"]) < 0.01
    )


def score(case, call):
    exp = case["expected"]
    try:
        got = call(case["text"])
    except ValueError as exc:       # parser asked for clarification / rejected
        return exp == "clarify", f"refused: {exc}"
    if exp == "clarify":
        return False, f"should have refused, got {got}"
    want = _expected_list(exp)
    if not isinstance(got, list) or len(got) != len(want):
        return False, f"expected {len(want)} transaction(s), got {got}"
    for got_item, want_item in zip(got, want):
        if not _items_match(got_item, want_item):
            return False, f"mismatch: expected {want}, got {got}"
    return True, str(got)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--cases", default=str(Path(__file__).with_name("parser_cases.jsonl")))
    ap.add_argument("--sleep", type=float, default=0.3, help="seconds between API calls")
    a = ap.parse_args()
    cases = load(a.cases)

    if a.dry:  # perfect oracle: proves dataset + scorer are consistent
        def call(text):
            c = next(x for x in cases if x["text"] == text)["expected"]
            if c == "clarify": raise ValueError("clarify")
            return _expected_list(c)
    else:
        if not os.getenv("GROQ_API_KEY"):
            sys.exit("Set GROQ_API_KEY first (or use --dry).")
        def call(text):
            time.sleep(a.sleep)
            return parser.parse_transactions(text)

    buckets = {"extract": [0, 0], "multi": [0, 0], "clarify": [0, 0]}
    fails = []
    for c in cases:
        ok, detail = score(c, call)
        exp = c["expected"]
        if exp == "clarify":
            b = "clarify"
        elif isinstance(exp, list) and len(exp) > 1:
            b = "multi"
        else:
            b = "extract"
        buckets[b][1] += 1; buckets[b][0] += ok
        if not ok: fails.append((c["text"], c["expected"], detail))
    total = sum(v[1] for v in buckets.values()); passed = sum(v[0] for v in buckets.values())
    print(f"Overall: {passed}/{total} = {passed/total:.0%}")
    for k, (p, n) in buckets.items(): print(f"  {k:8s} {p}/{n}")
    for t, e, d in fails: print(f"\nFAIL: {t}\n  expected: {e}\n  got:      {d}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
