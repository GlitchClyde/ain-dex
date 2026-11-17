"""Markdown report with Dex verification stamps."""
from __future__ import annotations

from datetime import date

from .models import HealthIssue, Insight, SchemaIndex, Status, Verdict
from .dex import insight_status

ICON = {Status.VERIFIED: "✅", Status.ASSUMED: "🟡", Status.LOW_SAMPLE: "🟠", Status.MISMATCH: "⛔"}


def build_markdown(
    filename: str,
    schema: SchemaIndex,
    health: list[HealthIssue],
    insights: list[Insight],
    verdicts: list[list[Verdict]],
    narrative: str | None = None,
    narrative_flags: list[str] | None = None,
) -> str:
    lines = [f"# Ain.dex Report: {filename}", f"_Generated {date.today().isoformat()}_", ""]
    lines += ["## Dataset", f"{schema.rows:,} rows x {len(schema.columns)} columns.", ""]
    lines += ["| Column | Kind | Missing | Unique |", "|---|---|---|---|"]
    lines += [f"| {c.name} | {c.kind} | {c.nulls} | {c.unique} |" for c in schema.columns]

    lines += ["", "## Dex data health"]
    lines += [f"- **{h.severity}**: {h.message}" for h in health] or ["- No issues found."]

    if narrative:
        lines += ["", "## Executive summary (drafted by Ain)", narrative]
        if narrative_flags:
            lines += ["", f"> ⚠️ Dex could not verify these numbers: {', '.join(narrative_flags)}"]

    lines += ["", "## Insights"]
    for ins, vs in zip(insights, verdicts):
        st = insight_status(vs)
        lines += ["", f"### {ICON[st]} {ins.title}  `[{st.value}]`"]
        if st is Status.MISMATCH:
            lines.append("Blocked by Dex: " + "; ".join(v.message for v in vs if v.status is Status.MISMATCH))
            continue
        lines.append(ins.text)
        lines += [f"- {v.lineage} ({v.status.value})" for v in vs]
    return "\n".join(lines) + "\n"
