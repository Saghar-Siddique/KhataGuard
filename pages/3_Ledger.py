import streamlit as st
from backend.core import list_customers, get_customer_statement, undo_last_entry, get_recent_audit_log

st.set_page_config(page_title="Ledger - KhataGuard", page_icon="📒", layout="wide")
st.title("📒 Customer Ledger")
st.caption("Real database se customer ka complete hisaab.")
customers=[dict(x) for x in list_customers()]
if not customers: st.info("Pehle customer add karein."); st.stop()
ids={int(c['id']):c for c in customers}
default_id=st.session_state.get('selected_customer_id',next(iter(ids)))
if default_id not in ids: default_id=next(iter(ids))
selected_id=st.selectbox("Customer Chunain",list(ids),index=list(ids).index(default_id),format_func=lambda i: ids[i]['name'])
try:
    s=get_customer_statement(ids[selected_id]['name'])
    c1,c2,c3=st.columns(3)
    c1.metric("Total Sale",f"Rs. {sum(float(x['amount']) for x in s['ledger'] if x['type']=='sale' and not x.get('voided')):,.2f}")
    c2.metric("Total Paid",f"Rs. {sum(float(x['amount']) for x in s['ledger'] if x['type']=='payment' and not x.get('voided')):,.2f}")
    c3.metric("Outstanding",f"Rs. {float(s['balance']):,.2f}")
    st.subheader(f"📜 {s['customer_name']} ki Transaction History")
    rows=[dict(x) for x in s['ledger']]
    if rows: st.dataframe(rows,use_container_width=True,hide_index=True)
    else: st.info("Is customer ki abhi koi transaction nahi hai.")
except Exception as exc: st.error(f"Ledger load nahi hua: {exc}")

st.divider()
st.subheader("↩️ Undo Last Entry")
st.caption("Aakhri entry void ho jayegi — delete nahi hogi, ledger mein 'voided' mark ke saath rehti hai.")
if st.session_state.get("undo_message"):
    st.success(st.session_state["undo_message"])
    st.session_state["undo_message"] = ""
if st.button("↩️ Undo last entry", use_container_width=True):
    st.session_state["confirm_undo"] = True
if st.session_state.get("confirm_undo"):
    st.warning("Aakhri entry void karni hai?")
    u1, u2 = st.columns(2)
    if u1.button("✅ Yes, undo it", use_container_width=True):
        try:
            result = undo_last_entry()
            st.session_state["undo_message"] = f"Undo ho gaya: {result['type']} Rs. {result['amount']:,.2f} void — balance Rs. {result['balance_before']:,.2f} se Rs. {result['balance_after']:,.2f} ho gaya."
            st.session_state["confirm_undo"] = False
            st.rerun()
        except Exception as exc:
            st.error(f"Undo nahi hua: {exc}")
            st.session_state["confirm_undo"] = False
    if u2.button("Cancel", use_container_width=True):
        st.session_state["confirm_undo"] = False
        st.rerun()

st.divider()
st.subheader("📜 Audit Log")
audit_rows = [dict(x) for x in get_recent_audit_log(20)]
if audit_rows: st.dataframe(audit_rows, use_container_width=True, hide_index=True)
else: st.info("Abhi koi audit entry nahi hai.")
