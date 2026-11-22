"""Settings — Issue #3 daily API cost guard.

Daily spend limit (default Rs 50/day) plus today's usage log. The limit
resets at Pakistan midnight. Costs are estimates from Groq's published
rates, not a bill.
"""
from __future__ import annotations

import streamlit as st

from backend.features import cost_guard

st.set_page_config(page_title="Settings - KhataGuard", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings — API Cost Guard")
st.caption(
    "Har voice note / image Groq ki paid APIs use karta hai. Daily limit "
    "lagayein taake kharcha kabhi haath se na nikle."
)

current_limit = cost_guard.get_daily_limit_pkr()
today_spend = cost_guard.get_today_spend_pkr()

c1, c2 = st.columns(2)
c1.metric("Today's AI spend (estimated)", f"Rs {today_spend:.2f}")
c2.metric("Daily limit", f"Rs {current_limit:.0f}")

st.subheader("Daily spend limit")
new_limit = st.number_input(
    "Daily limit (Rs)",
    min_value=1.0,
    max_value=10000.0,
    value=float(current_limit),
    step=5.0,
    help="Is se zyada estimated spend hone par voice aur image band ho jayenge; text entry chalti rahegi.",
)
if st.button("Save limit", use_container_width=True):
    try:
        saved = cost_guard.set_daily_limit_pkr(new_limit)
        st.success(f"Daily limit Rs {saved:.0f} save ho gayi.")
    except ValueError as exc:
        st.error(str(exc))

st.divider()
st.subheader("Today's usage log")
rows = cost_guard.get_recent_usage(limit=20)
if not rows:
    st.info("Aaj abhi koi AI usage record nahi hui.")
else:
    st.table(
        [
            {
                "Time (UTC)": row["created_at"],
                "Kind": row["kind"],
                "Cost (Rs)": f"{row['cost_pkr']:.4f}",
                "Detail": row["detail"],
            }
            for row in rows
        ]
    )

st.divider()
st.caption(
    "Rates (Groq published, Oct 2026): Whisper turbo $0.04/audio hour "
    "(10s minimum), Whisper v3 $0.111/hr, gpt-oss-20b $0.075/$0.30 per 1M "
    "tokens, qwen3.6-27b $0.60/$3.00 per 1M tokens. USD→PKR 280 assumed. "
    "Yeh estimates hain, bill nahi. TTS app mein abhi hai hi nahi — jab "
    "add hoga to woh bhi isi limit ke andar aayega."
)
