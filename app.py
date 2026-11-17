"""Ain.dex: Streamlit UI. Run with:  streamlit run app.py"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ain_dex import ain, dex, llm, report, security
from ain_dex.models import Status

load_dotenv()
SAMPLE = Path(__file__).parent / "sample_data" / "sales_sample.csv"
ICON = {Status.VERIFIED: "✅", Status.ASSUMED: "🟡", Status.LOW_SAMPLE: "🟠", Status.MISMATCH: "⛔"}
SEV_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}

st.set_page_config(page_title="Ain.dex", page_icon="🔍", layout="wide")


@st.cache_data(show_spinner=False)
def run_pipeline(filename: str, data: bytes):
    raw = security.load_dataframe(filename, data)
    clean = ain.prepare(raw)
    schema = ain.index_schema(clean)
    health = dex.health_check(raw, clean, schema)
    insights = ain.generate_insights(clean, schema)
    verdicts = dex.audit(clean, insights, schema)
    return {"clean": clean, "schema": schema, "health": health, "insights": insights, "verdicts": verdicts}


# ----------------------------- sidebar ------------------------------------ #
with st.sidebar:
    st.header("Ain.dex")
    st.caption("Ain analyses. Dex audits.")
    upload = st.file_uploader("Upload .csv or .xlsx (max 10 MB)", type=["csv", "xlsx"])
    use_sample = st.checkbox("Use bundled sample dataset", value=upload is None, disabled=upload is not None)
    show_audit = st.toggle("Show Dex audit trail", value=True)
    st.divider()
    st.caption("Files are processed in memory on your machine. Nothing is stored.")

if upload is not None:
    filename, data = upload.name, upload.getvalue()
elif use_sample and SAMPLE.exists():
    filename, data = SAMPLE.name, SAMPLE.read_bytes()
else:
    st.title("Ain.dex")
    st.info("Upload a spreadsheet in the sidebar, or tick the sample dataset to begin.")
    st.stop()

try:
    result = run_pipeline(filename, data)
except security.UploadRejected as exc:
    st.error(f"Upload rejected: {exc}")
    st.stop()

clean, schema = result["clean"], result["schema"]
health, insights, verdicts = result["health"], result["insights"], result["verdicts"]
statuses = [dex.insight_status(v) for v in verdicts]
verified_pct = (sum(s is Status.VERIFIED for s in statuses) / len(statuses) * 100) if statuses else 0

st.title(f"Ain.dex  |  {filename}")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Rows", f"{schema.rows:,}")
k2.metric("Columns", len(schema.columns))
k3.metric("Dex health flags", len(health))
k4.metric("Fully verified insights", f"{verified_pct:.0f}%")

tab_ain, tab_dex, tab_scope, tab_export = st.tabs(["Ain Workspace", "Dex Audit", "Scope Check", "Export"])

with tab_ain:
    st.subheader("Schema index")
    st.dataframe(
        pd.DataFrame([vars(c) for c in schema.columns]).rename(columns={"null_pct": "missing %"}),
        use_container_width=True,
        hide_index=True,
    )
    st.subheader("Insights")
    if not insights:
        st.warning("Ain found nothing to analyse. Does the file contain numeric columns?")
    for ins, vs, status in zip(insights, verdicts, statuses):
        with st.container(border=True):
            st.markdown(f"**{ICON[status]} {ins.title}**  `{status.value}`")
            if status is Status.MISMATCH:
                st.error("Blocked by Dex: " + "; ".join(v.message for v in vs if v.status is Status.MISMATCH))
                continue
            st.write(ins.text)
            if ins.chart:
                kind, series = ins.chart
                (st.bar_chart if kind == "bar" else st.line_chart)(series)
            if show_audit:
                with st.expander("Dex audit trail"):
                    for v in vs:
                        st.markdown(f"- `{v.claim.op}` {ICON[v.status]} {v.message}  \n  _{v.lineage}_")

with tab_dex:
    st.subheader("Data health check")
    if not health:
        st.success("No data quality issues detected.")
    for h in health:
        st.markdown(f"{SEV_ICON[h.severity]} **{h.severity}**: {h.message}")
    st.subheader("Claim-by-claim verdicts")
    rows = [
        {"insight": i.title, "claim": v.claim.op, "ain_value": v.claim.value, "dex_value": v.recomputed, "status": v.status.value, "lineage": v.lineage}
        for i, vs in zip(insights, verdicts)
        for v in vs
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

with tab_scope:
    st.subheader("Can this data answer my question?")
    q = st.text_input("Ask a business question", placeholder="e.g. What is our customer acquisition cost?")
    if q:
        g = dex.check_scope(q, schema)
        (st.success if g.supported else st.error)(g.message)

with tab_export:
    st.subheader("Executive summary")
    if llm.available():
        if st.button("Draft summary with Claude (sends verified aggregates only)"):
            facts = [f"{i.title}: {i.text}" for i, s in zip(insights, statuses) if s is not Status.MISMATCH]
            try:
                text = llm.draft_narrative(facts)
                allowed = [v.claim.value for vs in verdicts for v in vs]
                st.session_state["narr"] = (text, dex.audit_narrative(text, allowed))
            except Exception as exc:  # network/auth errors: show type only, never the key
                st.error(f"Could not draft summary ({type(exc).__name__}). Check your API key and connection.")
    else:
        st.caption("Optional: set ANTHROPIC_API_KEY in a .env file to enable AI-drafted summaries. The app works fully without it.")
    narr, flags = st.session_state.get("narr", (None, None))
    if narr:
        st.write(narr)
        if flags:
            st.warning("Dex could not verify these numbers: " + ", ".join(flags))
        else:
            st.success("Dex verified every number in this summary.")

    md = report.build_markdown(filename, schema, health, insights, verdicts, narr, flags)
    st.download_button("Download verified report (.md)", md, file_name="ain_dex_report.md", mime="text/markdown")
    table = pd.DataFrame(
        [{"insight": i.title, "claim": v.claim.op, "value": v.claim.value, "status": v.status.value, "lineage": v.lineage} for i, vs in zip(insights, verdicts) for v in vs]
    )
    safe = security.sanitize_for_export(table)
    st.download_button("Download insights (.csv)", safe.to_csv(index=False), file_name="ain_dex_insights.csv", mime="text/csv")
