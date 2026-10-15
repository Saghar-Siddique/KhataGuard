KHATAGUARD FINAL - SAFE PATCH

This version keeps the existing KhataGuard UI, database backend, Part 3 parser,
Voice/Image flow, and Reports unchanged in behavior, while fixing Groq token-limit
issues and making Voice/Image API errors explicit.

IMPORTANT DATA NOTE:
- This ZIP contains a DEMO khata.db (5 customers, 20 transactions) for testing only.
- If you already have a khata.db with real data, do NOT overwrite it: delete the
  khata.db from this ZIP before extracting over your folder, or set the environment
  variable KHATA_DB_PATH to your real database file (e.g. KHATA_DB_PATH=D:\khata\khata.db).
- khata.db is listed in .gitignore, so it will not be committed to git.

Groq settings:
- GROQ_MODEL = openai/gpt-oss-20b
- GROQ_SPEECH_MODEL = whisper-large-v3-turbo
- GROQ_VISION_MODEL = qwen/qwen3.6-27b

The Vision request now stays below the 1,000-token output limit seen in the
current Groq account response. The Part 3 parser is also capped below 1,000 so
voice/image parsing does not hit the same class of error.

Run:
  py -m pip install -r requirements.txt
  py -m streamlit run app.py

See CHANGES.md for the fixes in this version, and evals/ for the parser accuracy eval.
