from __future__ import annotations

import os
from datetime import date

import streamlit as st

from backend.core import (
    create_customer,
    get_customer_balance,
    list_customers,
    record_payment,
    record_sale,
)
from backend.features import cost_guard
from backend.features.integration import save_confirmed_transactions
from backend.features.matching import rank_customers
from backend.features.media import (
    DEFAULT_SPEECH_MODEL,
    DEFAULT_VISION_MODEL,
    MediaProcessingError,
    ocr_handwritten_image,
    parse_many_with_existing_ai,
    transcribe_audio,
)

try:
    from backend.ai.parser import parse_transactions
except Exception:
    parse_transactions = None


st.set_page_config(page_title="Voice & Image - KhataGuard", page_icon="🎙️", layout="wide")
st.title("🎙️ Part 4 — Voice & Image")
st.caption("Voice/image is converted into a draft first. Nothing is saved until you confirm it.")


def setting(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name) or os.getenv(name) or default
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return os.getenv(name) or default


def set_drafts(source_text: str, source_kind: str) -> None:
    """Parse every transaction in the text into a batch of drafts (Issue #2)."""
    if parse_transactions is None:
        raise MediaProcessingError(
            "backend.ai.parser.parse_transactions nahi mila. Part 3 parser ko project mein same path par rakhein."
        )
    results = parse_many_with_existing_ai(
        text=source_text,
        parser=parse_transactions,
        api_key=setting("GROQ_API_KEY"),
        text_model=setting("GROQ_MODEL", "openai/gpt-oss-20b"),
    )
    st.session_state["p4_draft_n"] = st.session_state.get("p4_draft_n", 0) + 1
    st.session_state["p4_drafts"] = [
        {
            "customer": result.customer,
            "sale": result.sale,
            "paid": result.paid,
            "date": date.today(),
            "source_text": source_text,
            "source_kind": source_kind,
        }
        for result in results
    ]


api_key = setting("GROQ_API_KEY")
if not api_key:
    st.warning("Streamlit Secrets mein GROQ_API_KEY add karein. Manual database data ko yeh page change nahi karta.")

# Issue #3 — daily AI cost guard: show today's spend and block voice/image
# when the cap is hit. Text entry below keeps working as the fallback.
daily_limit = cost_guard.get_daily_limit_pkr()
today_spend = cost_guard.get_today_spend_pkr()
capped = today_spend >= daily_limit
st.caption(f"Today's AI spend: Rs {today_spend:.2f} / Rs {daily_limit:.0f} daily limit")
if capped:
    st.error(
        f"Aaj ki Rs {daily_limit:.0f} daily AI limit khatam ho gayi hai "
        f"(spend Rs {today_spend:.2f}). Voice aur image band hain — "
        "neeche text se entry karein."
    )

voice_tab, image_tab = st.tabs(["🎤 Voice", "🖼️ Handwritten Image"])

with voice_tab:
    st.subheader("Voice → Text → Existing AI Parser")
    language_label = st.selectbox("Voice language", ["Auto", "Urdu", "English"], key="p4_lang")
    language = {"Auto": None, "Urdu": "ur", "English": "en"}[language_label]

    live_audio = st.audio_input("Record transaction", key="p4_live_audio")
    uploaded_audio = st.file_uploader(
        "Or upload audio",
        type=["wav", "mp3", "m4a"],
        key="p4_audio_upload",
    )

    left, right = st.columns(2)
    with left:
        if st.button("Process live recording", use_container_width=True, disabled=live_audio is None or not api_key or capped):
            try:
                with st.spinner("Voice transcribe aur parse ho rahi hai..."):
                    text = transcribe_audio(
                        audio_bytes=live_audio.getvalue(),
                        filename="recording.wav",
                        api_key=api_key,
                        speech_model=setting("GROQ_SPEECH_MODEL", DEFAULT_SPEECH_MODEL),
                        language=language,
                    )
                    st.session_state["p4_transcript"] = text
                    set_drafts(text, "voice")
                st.success("Draft ready hai. Neeche verify karein.")
            except Exception as exc:
                st.error(str(exc))

    with right:
        if st.button("Process uploaded audio", use_container_width=True, disabled=uploaded_audio is None or not api_key or capped):
            try:
                with st.spinner("Uploaded audio transcribe aur parse ho rahi hai..."):
                    text = transcribe_audio(
                        audio_bytes=uploaded_audio.getvalue(),
                        filename=uploaded_audio.name,
                        api_key=api_key,
                        speech_model=setting("GROQ_SPEECH_MODEL", DEFAULT_SPEECH_MODEL),
                        language=language,
                    )
                    st.session_state["p4_transcript"] = text
                    set_drafts(text, "voice_upload")
                st.success("Draft ready hai. Neeche verify karein.")
            except Exception as exc:
                st.error(str(exc))

    st.text_area(
        "Transcript (read-only preview)",
        value=st.session_state.get("p4_transcript", ""),
        height=110,
        disabled=True,
    )

    # Issue #3 fallback: text entry keeps working even when the cap is hit.
    st.divider()
    st.subheader("⌨️ Text se entry (voice limit khatam hone par bhi kaam karta hai)")
    typed_text = st.text_area(
        "Transaction likhein, jaise: Ahmed ne 5000 ka saman liya aur 2000 diye",
        value="",
        height=80,
        key="p4_typed_text",
    )
    if st.button("Parse typed text", use_container_width=True, disabled=not typed_text.strip() or not api_key):
        try:
            with st.spinner("Text parse ho raha hai..."):
                set_drafts(typed_text.strip(), "text")
            st.success("Draft ready hai. Neeche verify karein.")
        except Exception as exc:
            st.error(str(exc))

with image_tab:
    st.subheader("Handwritten Khata Image → OCR → Existing AI Parser")
    st.caption("Vision model sirf image ko text mein read karta hai; transaction rules Part 3 parser hi apply karta hai.")
    image = st.file_uploader("Upload ledger image", type=["png", "jpg", "jpeg"], key="p4_image")
    if image is not None:
        st.image(image, caption="Uploaded ledger page", width=500)
    if st.button("Read & understand image", use_container_width=True, disabled=image is None or not api_key or capped):
        try:
            with st.spinner("Handwriting read aur transaction parse ho rahi hai..."):
                ocr_text = ocr_handwritten_image(
                    image_bytes=image.getvalue(),
                    filename=image.name,
                    api_key=api_key,
                    vision_model=setting("GROQ_VISION_MODEL", DEFAULT_VISION_MODEL),
                )
                st.session_state["p4_ocr_text"] = ocr_text
                set_drafts(ocr_text, "image")
            st.success("Image draft ready hai. Neeche verify karein.")
        except Exception as exc:
            st.error(str(exc))

    st.text_area(
        "OCR text (read-only preview)",
        value=st.session_state.get("p4_ocr_text", ""),
        height=120,
        disabled=True,
    )

st.divider()
st.subheader("✅ Verify before saving")

drafts = st.session_state.get("p4_drafts")
NEW_CUSTOMER = "__new__"
if not drafts:
    st.info("Voice ya image process karne ke baad editable draft yahan appear hoga.")
else:
    n = st.session_state.get("p4_draft_n", 0)
    all_customers = [dict(c) for c in list_customers()]
    by_id = {int(c["id"]): c for c in all_customers}
    if len(drafts) > 1:
        st.caption(f"{len(drafts)} transactions mileen — ek hi confirmation se saari save hongi.")
    rows = []
    with st.form("p4_review_form"):
        for i, draft in enumerate(drafts):
            matches = rank_customers(draft["customer"], all_customers)
            exact = [m for m in matches if m.kind == "exact"]

            # Options: explicit placeholder, ranked existing customers, then "create new".
            options = [None] + [m.customer_id for m in matches] + [NEW_CUSTOMER]
            if len(exact) == 1:
                default_index = options.index(exact[0].customer_id)
            elif not matches:
                default_index = options.index(NEW_CUSTOMER)
            else:
                default_index = 0  # similar names found but none exact: user must choose

            def customer_label(option, _draft=draft, _matches=matches):
                if option is None:
                    return "— Select customer —"
                if option == NEW_CUSTOMER:
                    return f"➕ Create NEW customer '{_draft['customer']}'"
                c = by_id[option]
                match = next((m for m in _matches if m.customer_id == option), None)
                hint = f"  (similarity {match.score:.0%})" if match and match.kind != "exact" else ""
                return f"{c['name']}{hint}"

            st.subheader(f"Transaction {i + 1} — {draft['customer']}")
            st.caption(f"Source: {draft['source_kind']}. Save se pehle har field check karein.")
            if matches and not exact:
                st.warning(
                    f"AI ne naam '{draft['customer']}' suna/parha. Milte-julte customers mil gaye hain; "
                    "sahi customer select karein, ya naya banayein."
                )
            choice = st.selectbox(
                "Customer",
                options,
                index=default_index,
                format_func=customer_label,
                key=f"p4_customer_choice_{n}_{i}",
            )
            new_name = st.text_input(
                "New customer name (sirf tab use hoga jab 'Create NEW' select ho)",
                value=draft["customer"],
                key=f"p4_new_name_{n}_{i}",
            )
            c1, c2 = st.columns(2)
            sale = c1.number_input("Sale (Rs.)", min_value=0.0, value=float(draft["sale"]), step=100.0, key=f"p4_sale_{n}_{i}")
            paid = c2.number_input("Paid / Received (Rs.)", min_value=0.0, value=float(draft["paid"]), step=100.0, key=f"p4_paid_{n}_{i}")
            advance = st.checkbox(
                "Advance payment (customer ke outstanding balance se zyada payment)",
                value=False,
                key=f"p4_adv_{n}_{i}",
            )
            rows.append({
                "choice": choice, "new_name": new_name,
                "sale": sale, "paid": paid, "advance": advance,
                "draft": draft,
            })
        tx_date = st.date_input("Transaction date", value=drafts[0]["date"], key=f"p4_date_{n}")
        note = st.text_input("Description / note", value=drafts[0]["source_text"][:250], key=f"p4_note_{n}")
        save = st.form_submit_button(
            f"Confirm & Save All ({len(drafts)})", use_container_width=True
        )

    if save:
        try:
            items = []
            for j, row in enumerate(rows):
                if row["choice"] is None:
                    raise ValueError(f"Transaction {j + 1}: pehle customer select karein (ya 'Create NEW' chunein).")
                if row["choice"] == NEW_CUSTOMER:
                    name = row["new_name"].strip()
                    if not name:
                        raise ValueError(f"Transaction {j + 1}: naye customer ka naam required hai.")
                    if row["sale"] <= 0 and not row["advance"]:
                        raise ValueError(
                            f"Transaction {j + 1}: naye customer ka balance 0 hai, is liye sirf payment "
                            "save nahi ho sakti. Sale add karein ya 'Advance payment' tick karein."
                        )
                    customer_id = create_customer(name=name)  # raises if the name already exists
                    customer = {"id": customer_id, "name": " ".join(name.split())}
                else:
                    customer = by_id[int(row["choice"])]
                items.append({
                    "customer_id": int(customer["id"]),
                    "customer_name": str(customer["name"]),
                    "sale": row["sale"],
                    "paid": row["paid"],
                    "allow_overpayment": row["advance"],
                })
            saved = save_confirmed_transactions(
                items,
                transaction_date=tx_date,
                description=note,
                record_sale=record_sale,
                record_payment=record_payment,
                get_balance=get_customer_balance,
            )
            st.session_state.pop("p4_drafts", None)
            st.success(f"{len(saved)} transactions database mein save ho gayeen.")
            st.write(saved)
        except Exception as exc:
            st.error(f"Save nahi hui: {exc}")
