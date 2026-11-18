"""Dex: the auditor. Deterministic, skeptical, conservative.

Rules Dex enforces:
1. No mental math: every claim is re-computed with a separate pure-Python
   implementation (Ain uses pandas), and must match.
2. Strict data boundaries: questions needing absent fields are refused.
3. Source lineage: every verdict says which column and how many rows.
4. Data hygiene: flaws are disclosed before insights are trusted.
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
import numbers
import re
import statistics

import pandas as pd

from .models import (
    SEVERITY,
    Claim,
    GuardrailResult,
    HealthIssue,
    Insight,
    SchemaIndex,
    Status,
    Verdict,
)

MIN_SAMPLE = 30
ASSUMED_NULL_PCT = 0.05
REL_TOL = 1e-6
_NULL_TOKENS = {"", "nan", "none", "null", "n/a", "na", "#n/a", "-"}


# --------------------------------------------------------------------------- #
# Independent recomputation (plain Python, no pandas aggregation)
# --------------------------------------------------------------------------- #
def _num_values(df: pd.DataFrame, col: str) -> list[float]:
    return [float(v) for v in df[col].tolist() if v is not None and v == v]


def _recompute(df: pd.DataFrame, claim: Claim):
    """Return (value, winners_or_None, n_rows) or None when it cannot be computed."""
    if claim.column not in df.columns:
        return None
    op = claim.op

    if op in {"count", "sum", "mean", "median", "outlier_count"}:
        vals = _num_values(df, claim.column)
        n = len(vals)
        if n == 0:
            return None
        if op == "count":
            return float(n), None, n
        if op == "sum":
            return math.fsum(vals), None, n
        if op == "mean":
            return math.fsum(vals) / n, None, n
        if op == "median":
            return float(statistics.median(vals)), None, n
        if n < 4:
            return None
        q1, _, q3 = statistics.quantiles(vals, n=4, method="inclusive")
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return float(sum(1 for v in vals if v < lo or v > hi)), None, n

    if op in {"top_group_sum", "top_group_share"}:
        if not claim.group_by or claim.group_by not in df.columns:
            return None
        totals: dict[str, float] = {}
        n = 0
        for g, v in zip(df[claim.group_by].tolist(), df[claim.column].tolist()):
            if g is None or g != g or v is None or v != v:
                continue
            totals[str(g)] = totals.get(str(g), 0.0) + float(v)
            n += 1
        if not totals:
            return None
        best = max(totals.values())
        winners = {k for k, t in totals.items() if math.isclose(t, best, rel_tol=1e-9)}
        if op == "top_group_sum":
            return best, winners, n
        total = math.fsum(totals.values())
        if best <= 0 or total <= 0:
            return None
        return best / total * 100, winners, n

    if op == "period_change_pct":
        if not claim.date_column or claim.date_column not in df.columns:
            return None
        sums: dict[tuple[int, int], float] = {}
        n = 0
        for t, v in zip(df[claim.date_column].tolist(), df[claim.column].tolist()):
            if t is None or t != t or v is None or v != v:
                continue
            key = (t.year, t.month)
            sums[key] = sums.get(key, 0.0) + float(v)
            n += 1
        keys = sorted(sums)
        if len(keys) < 2:
            return None
        prev, last = sums[keys[-2]], sums[keys[-1]]
        if prev == 0:
            return None
        return (last - prev) / abs(prev) * 100, None, n

    return None


def _parse_raw_number(v):
    """Dex's own number parser. Deliberately separate from Ain's pandas-based cleaning."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, numbers.Real):
        f = float(v)
        return f if math.isfinite(f) else None
    s = str(v).strip()
    if s.lower() in _NULL_TOKENS:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    for ch in "$€£₱,% \u00a0":
        s = s.replace(ch, "")
    try:
        f = float(s)
    except ValueError:
        return None
    if not math.isfinite(f):
        return None
    return -f if neg else f


_ISO_DATE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T].*)?$")
_SLASH_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:[ T].*)?$")


def _parse_raw_date(v):
    """Dex's own date parser (pure Python). Ambiguous dd/mm vs mm/dd is read month-first
    unless one part is >12. Returns a date, or None when unreadable."""
    if v is None or v != v:
        return None
    if hasattr(v, "year") and hasattr(v, "month"):
        return v
    s = str(v).strip()
    if s.lower() in _NULL_TOKENS:
        return None
    try:
        m = _ISO_DATE.match(s)
        if m:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        m = _SLASH_DATE.match(s)
        if m:
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            month, day = (b, a) if a > 12 else (a, b)
            return dt.date(y, month, day)
    except ValueError:
        return None
    return None


def _clean_label(v):
    if v is None or v != v:
        return None
    s = str(v).strip()
    return None if s.lower() in _NULL_TOKENS else s


def _shadow_frame(raw: pd.DataFrame, claim: Claim) -> pd.DataFrame | None:
    """Rebuild only the columns a claim needs, straight from the raw values."""
    if claim.column not in raw.columns:
        return None
    data = {claim.column: [_parse_raw_number(v) for v in raw[claim.column].tolist()]}
    if claim.date_column:
        if claim.date_column not in raw.columns:
            return None
        data[claim.date_column] = [_parse_raw_date(v) for v in raw[claim.date_column].tolist()]
    if claim.group_by:
        if claim.group_by not in raw.columns:
            return None
        data[claim.group_by] = [_clean_label(v) for v in raw[claim.group_by].tolist()]
    return pd.DataFrame(data)


def _latest_month_partial(df: pd.DataFrame, date_col: str | None):
    """Return the last date if the latest month looks incomplete (heuristic), else None."""
    if not date_col or date_col not in df.columns:
        return None
    dates = [t for t in df[date_col].tolist() if t is not None and t == t]
    if not dates:
        return None
    last = max(dates)
    if last.day < 0.9 * calendar.monthrange(last.year, last.month)[1]:
        return last
    return None


def verify_claim(
    df: pd.DataFrame, claim: Claim, schema: SchemaIndex | None = None, raw: pd.DataFrame | None = None
) -> Verdict:
    result = _recompute(df, claim)
    lineage_cols = f"Col [{claim.column}]"
    if claim.group_by:
        lineage_cols += f" by [{claim.group_by}]"
    if claim.date_column:
        lineage_cols += f" over [{claim.date_column}]"

    if result is None:
        return Verdict(claim, Status.MISMATCH, "Dex could not independently recompute this claim.", f"Source: {lineage_cols}")

    value, winners, n = result
    nulls = int(df[claim.column].isna().sum()) if claim.column in df.columns else 0
    lineage = f"Source: {lineage_cols}, n={n:,} rows used, {nulls:,} null(s) excluded"

    if not math.isclose(claim.value, value, rel_tol=REL_TOL, abs_tol=1e-9):
        return Verdict(
            claim,
            Status.MISMATCH,
            f"Ain claimed {claim.value:,.4f} but Dex computed {value:,.4f}.",
            lineage,
            value,
        )
    if winners is not None and claim.group not in winners:
        return Verdict(claim, Status.MISMATCH, f"Ain named '{claim.group}' but Dex found {sorted(winners)}.", lineage, value)

    # Second, independent check: re-parse the raw cells so a bug in Ain's cleaning cannot hide.
    if raw is not None:
        shadow = _shadow_frame(raw, claim)
        if shadow is not None:
            again = _recompute(shadow, claim)
            ok = (
                again is not None
                and math.isclose(claim.value, again[0], rel_tol=REL_TOL, abs_tol=1e-9)
                and (again[1] is None or claim.group in again[1])
            )
            if not ok:
                got = "nothing" if again is None else f"{again[0]:,.4f}"
                return Verdict(
                    claim,
                    Status.MISMATCH,
                    f"Cleaning disagreement: re-parsing the raw cells gives {got}, Ain used {claim.value:,.4f}.",
                    lineage,
                    value,
                )
            lineage += "; re-parsed from raw cells: matched"

    null_pct = nulls / len(df) if len(df) else 0.0
    if n < MIN_SAMPLE:
        return Verdict(claim, Status.LOW_SAMPLE, f"Only {n} rows support this claim (<{MIN_SAMPLE}).", lineage, value)
    partial = _latest_month_partial(df, claim.date_column) if claim.op == "period_change_pct" else None
    if partial is not None:
        return Verdict(
            claim,
            Status.ASSUMED,
            f"Matches, but data ends {partial:%Y-%m-%d}, so the latest month is incomplete and the change is not like-for-like.",
            lineage,
            value,
        )
    if null_pct > ASSUMED_NULL_PCT:
        return Verdict(
            claim,
            Status.ASSUMED,
            f"Matches, but {null_pct:.0%} of [{claim.column}] is missing and was excluded.",
            lineage,
            value,
        )
    return Verdict(claim, Status.VERIFIED, "Independently recomputed and matched.", lineage, value)


def audit(
    df: pd.DataFrame, insights: list[Insight], schema: SchemaIndex | None = None, raw: pd.DataFrame | None = None
) -> list[list[Verdict]]:
    return [[verify_claim(df, c, schema, raw) for c in i.claims] for i in insights]


def insight_status(verdicts: list[Verdict]) -> Status:
    if not verdicts:
        return Status.MISMATCH
    return max((v.status for v in verdicts), key=lambda s: SEVERITY[s])


# --------------------------------------------------------------------------- #
# Data health
# --------------------------------------------------------------------------- #
def _present(s: pd.Series) -> pd.Series:
    txt = s.fillna("").astype(str).str.strip().str.lower()
    return ~txt.isin(_NULL_TOKENS)


def health_check(raw: pd.DataFrame, clean: pd.DataFrame, schema: SchemaIndex) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    n = len(clean)
    if n < MIN_SAMPLE:
        issues.append(HealthIssue("warning", f"Small dataset: {n} rows. Statistics will be unreliable."))

    dups = int(clean.duplicated().sum())
    if dups:
        issues.append(HealthIssue("warning", f"{dups} fully duplicated row(s). They are counted in totals as-is."))

    for col in schema.columns:
        if col.nulls:
            sev = "critical" if col.null_pct >= 0.5 else "warning" if col.null_pct > ASSUMED_NULL_PCT else "info"
            issues.append(HealthIssue(sev, f"[{col.name}] has {col.nulls:,} missing value(s) ({col.null_pct:.1%})."))
        if col.unique == 1 and col.nulls < n:
            issues.append(HealthIssue("info", f"[{col.name}] has a single constant value."))

        if col.name in raw.columns and col.name in clean.columns:
            r, c = raw[col.name], clean[col.name]
            if pd.api.types.is_object_dtype(r) and not pd.api.types.is_object_dtype(c):
                lost = int(_present(r).sum()) - int(c.notna().sum())
                if lost > 0:
                    issues.append(
                        HealthIssue("warning", f"[{col.name}] had {lost} value(s) that could not be parsed as {col.kind}; treated as missing.")
                    )
            elif pd.api.types.is_object_dtype(r) and pd.api.types.is_object_dtype(c) and _present(r).any():
                share = pd.to_numeric(r[_present(r)], errors="coerce").notna().mean()
                if 0.05 < share < 0.8:
                    issues.append(HealthIssue("warning", f"[{col.name}] mixes numbers and text ({share:.0%} numeric); kept as text."))

        if col.kind == "numeric" and col.name in clean.columns and (clean[col.name] < 0).any():
            issues.append(HealthIssue("info", f"[{col.name}] contains negative values (refunds or errors?)."))
    return issues


# --------------------------------------------------------------------------- #
# Scope guardrail
# --------------------------------------------------------------------------- #
# (question regex, column-name regex that must exist, human description)
_REQUIREMENTS = [
    (r"customer acquisition cost|\bcac\b", r"cac|acquisition|marketing|ad[_ ]?spend", "marketing or acquisition spend"),
    (r"\bprofit(s|able|ability)?\b|\bmargins?\b", r"cost|expense|cogs", "cost or expense"),
    (r"\bchurn(ed)?\b|\bretention\b", r"churn|cancel|retention|status|active", "churn, cancellation or status"),
    (r"\bconversion(s)?\b", r"conversion|visitor|session|lead|click", "visitors, leads or conversions"),
]


def check_scope(question: str, schema: SchemaIndex) -> GuardrailResult:
    q = (question or "").strip().lower()
    if not q:
        return GuardrailResult(False, "Ask a question to check whether the data can answer it.")
    names = [c.name.lower() for c in schema.columns]
    for q_pat, col_pat, desc in _REQUIREMENTS:
        if re.search(q_pat, q) and not any(re.search(col_pat, n) for n in names):
            topic = re.search(q_pat, q).group(0)
            return GuardrailResult(
                False,
                f"[DEX GUARDRAIL: Insufficient data to calculate {topic}. Missing required field: {desc}.]",
                [desc],
            )
    return GuardrailResult(True, "Dex found no missing fields for this question. It can proceed, and results will still be audited.")


# --------------------------------------------------------------------------- #
# Narrative audit (for optional LLM-written summaries)
# --------------------------------------------------------------------------- #
_NUM = re.compile(r"(?<![\w.])-?\$?\d[\d,]*\.?\d*\s?(?:%|[KkMmBb]\b)?")
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}


def _parse_number(token: str) -> tuple[float, bool] | None:
    t = token.strip().replace("$", "").replace(",", "")
    mult = 1.0
    if t and t[-1].lower() in _SUFFIX and t[-1].isalpha():
        mult = _SUFFIX[t[-1].lower()]
        t = t[:-1]
    t = t.replace("%", "").strip()
    try:
        return float(t) * mult, "." in t
    except ValueError:
        return None


def audit_narrative(text: str, allowed: list[float]) -> list[str]:
    """Return numbers in `text` that do not match any Dex-verified value."""
    allowed_abs = [abs(a) for a in allowed]
    flagged: list[str] = []
    for m in _NUM.finditer(text):
        token = m.group(0).strip()
        parsed = _parse_number(token)
        if parsed is None:
            continue
        value, has_decimal = parsed
        if not has_decimal and value == int(value) and (0 <= value <= 10 or 1900 <= value <= 2100):
            continue  # counts like "3 insights" and years
        if any(math.isclose(abs(value), a, rel_tol=0.005, abs_tol=0.01) for a in allowed_abs):
            continue
        flagged.append(token)
    return flagged
