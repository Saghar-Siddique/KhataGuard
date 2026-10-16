# 📒 KhataGuard

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![CI](https://github.com/Saghar-Siddique/KhataGuard/actions/workflows/ci.yml/badge.svg)](https://github.com/Saghar-Siddique/KhataGuard/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-29%20passed-brightgreen)]()
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![Groq](https://img.shields.io/badge/AI-Groq%20%7C%20Whisper-F55036)]()

**AI-Powered Digital Khata** — speak or snap, and the ledger writes itself.

KhataGuard turns voice notes and receipt photos into ledger entries. Its AI
pipeline (Groq) understands Urdu, Roman Urdu and English, matches customers
fuzzily, and guards against duplicates and overpayments. Built for the way
Pakistani shopkeepers actually work — no typing, no English required.

## Features

- 🎙️ **Voice / Image input** → AI transaction parsing
  (Whisper speech-to-text + Qwen vision OCR + structured extraction)
- 👥 **Customers** — case-insensitive duplicate detection
- 💰 **Transactions** — overpayment guard with explicit advance-payment flow
- 📒 **Ledger** — per-customer history
- 📊 **Reports** — Plotly charts + PDF statements (ReportLab)
- 🧪 **Quality** — 29 regression tests, 32-case parser eval
  (Urdu / Roman Urdu / English, incl. must-clarify and prompt-injection cases)

## Tech stack

Python · Streamlit · SQLite · Plotly · Pillow · ReportLab · Groq API

## Quickstart

```bash
pip install -r requirements.txt
streamlit run app.py
```

Voice/Image features need a Groq API key:

```bash
export GROQ_API_KEY="your-key-here"
```

> ⚠️ Never commit your API key. `.gitignore` already covers
> `.streamlit/secrets.toml` and `khata.db`.

## Project structure

```
app.py                  # Home dashboard (metrics + navigation)
pages/                  # 1_Customers, 2_Transactions, 3_Ledger, 4_Voice_Image, 5_Reports
backend/
  database/             # SQLite init + schema (initialize_database)
  core/                 # customer summaries, business logic
  ai/                   # Groq-powered transaction parser
  features/             # voice/OCR matching, media, reports, demo-data seeding
tests/                  # regression tests (pytest)
evals/                  # parser accuracy eval (32 cases)
requirements.txt
KhataGuard_Mobile_Test.ipynb   # phone testing via Google Colab + LocalTunnel
```

## Demo data

`khata.db` is gitignored — the app creates a fresh database on first run via
`initialize_database()`. For a disposable demo, seed it from code:

```python
from backend.features.demo_data import generate_demo_data
generate_demo_data("demo_khata.db")   # refuses non-empty DBs by default
```

Keep a real database outside the code folder with:

```bash
export KHATA_DB_PATH="/path/to/your/khata.db"
```

## Mobile testing

Open `KhataGuard_Mobile_Test.ipynb` in Google Colab on your phone — it
installs dependencies, starts Streamlit on port 8501 and exposes it via
LocalTunnel. Put your Groq key in Colab Secrets (never in the notebook).

## Known limitations

- No authentication; SQLite on ephemeral hosts (Colab / Streamlit Cloud) loses data
- One transaction per input (multi-entry page photos are rejected)
- No audit log / undo; no API cost guard
- Urdu text in PDF statements needs an Arabic-shaping font (falls back to Helvetica)
