"""Ain: the junior data analyst. Ingests, indexes, and drafts insights.

Ain proposes numbers (Claims). It never gets the last word: Dex re-computes
every claim independently before anything is shown as verified.
"""
from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd

from .models import Claim, ColumnInfo, Insight, SchemaIndex

NULL_TOKENS = {"", "nan", "none", "null", "n/a", "na", "#n/a", "-"}
MAX_GROUPS = 20
_ID_NAME = re.compile(r"(^|[_\s])id$|^id([_\s]|$)|^index$", re.I)


def fmt(x: float) -> str:
    return f"{x:,.2f}"


# --------------------------------------------------------------------------- #
# Cleaning / type inference
# --------------------------------------------------------------------------- #
def coerce_numeric(series: pd.Series) -> pd.Series | None:
    """Parse '$1,234.50', '(500)', '12%' etc. Returns None if <80% of values parse."""
    if pd.api.types.is_bool_dtype(series):
        return None
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return None
    s = series.astype(object)
    present = s.notna()
    if present.sum() == 0:
        return None
    t = s.astype(str).str.strip()
    t = t.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    t = t.str.replace(r"[,$€£₱\s%]", "", regex=True)
    conv = pd.to_numeric(t.where(present), errors="coerce")
    conv = conv.replace([np.inf, -np.inf], np.nan)
    if conv[present].notna().mean() >= 0.8:
        return conv.astype(float)
    return None


def coerce_datetime(series: pd.Series) -> pd.Series | None:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return None
    present = series.notna()
    if present.sum() == 0:
        return None
    as_text = series.astype(str)
    if as_text[present].str.contains(r"\d", regex=True).mean() < 0.8:
        return None  # avoid parsing words like "May" as dates
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(series.where(present), errors="coerce", format="mixed")
    if not pd.api.types.is_datetime64_any_dtype(parsed):
        return None
    if parsed[present].notna().mean() >= 0.8:
        return parsed
    return None


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise null tokens and coerce numeric/date columns."""
    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
            s = s.astype(object)
            stripped = s.map(lambda v: v.strip() if isinstance(v, str) else v)
            is_null = stripped.map(lambda v: (isinstance(v, str) and v.lower() in NULL_TOKENS) or v is None)
            s = stripped.where(~is_null, np.nan)
            num = coerce_numeric(s)
            if num is not None:
                s = num
            else:
                dt = coerce_datetime(s)
                if dt is not None:
                    s = dt
        df[col] = s
    return df


# --------------------------------------------------------------------------- #
# Schema indexing
# --------------------------------------------------------------------------- #
def index_schema(df: pd.DataFrame) -> SchemaIndex:
    n = len(df)
    cols: list[ColumnInfo] = []
    for c in df.columns:
        s = df[c]
        nn = int(s.notna().sum())
        uniq = int(s.nunique(dropna=True))
        if pd.api.types.is_datetime64_any_dtype(s):
            kind = "datetime"
        elif pd.api.types.is_bool_dtype(s):
            kind = "boolean"
        elif pd.api.types.is_numeric_dtype(s):
            kind = "id" if (_ID_NAME.search(c) and nn and uniq / nn > 0.9) else "numeric"
        else:
            if nn and _ID_NAME.search(c) and uniq / nn > 0.9:
                kind = "id"
            elif 1 <= uniq <= MAX_GROUPS and (nn == 0 or uniq / nn <= 0.5):
                kind = "categorical"
            else:
                kind = "text"
        cols.append(ColumnInfo(c, kind, n - nn, uniq, (n - nn) / n if n else 0.0))
    return SchemaIndex(rows=n, columns=cols)


# --------------------------------------------------------------------------- #
# Insight generation (pandas implementation; Dex re-checks in plain Python)
# --------------------------------------------------------------------------- #
def _monthly_sums(df: pd.DataFrame, date_col: str, value_col: str) -> pd.Series:
    d = df[[date_col, value_col]].dropna()
    return d.groupby(d[date_col].dt.to_period("M"))[value_col].sum().sort_index()


def generate_insights(df: pd.DataFrame, schema: SchemaIndex, max_numeric: int = 3) -> list[Insight]:
    insights: list[Insight] = []
    nums = [c.name for c in schema.of_kind("numeric")][:max_numeric]
    cats = [c.name for c in schema.of_kind("categorical") if c.unique >= 2]
    dates = [c.name for c in schema.of_kind("datetime")]
    group = cats[0] if cats else None
    date = dates[0] if dates else None

    for col in nums:
        s = df[col].dropna()
        if s.empty:
            continue
        total, mean, med = float(s.sum()), float(s.mean()), float(s.median())
        insights.append(
            Insight(
                title=f"{col}: overall profile",
                text=f"Total {fmt(total)}; mean {fmt(mean)}; median {fmt(med)} across {len(s):,} records.",
                claims=[Claim("sum", col, total), Claim("mean", col, mean), Claim("median", col, med)],
            )
        )
        if len(s) >= 4:
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                out = int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())
                if out > 0:
                    insights.append(
                        Insight(
                            title=f"{col}: outliers",
                            text=f"{out} value(s) fall outside 1.5x the interquartile range and may be errors or exceptional events.",
                            claims=[Claim("outlier_count", col, float(out))],
                        )
                    )

    if group and nums:
        col = nums[0]
        g = df.dropna(subset=[group, col]).groupby(group)[col].sum()
        if len(g) >= 2:
            top, topv, total = str(g.idxmax()), float(g.max()), float(g.sum())
            claims = [Claim("top_group_sum", col, topv, group_by=group, group=top)]
            text = f"'{top}' leads {group} with {fmt(topv)} in {col}."
            if topv > 0 and total > 0:
                share = topv / total * 100
                claims.append(Claim("top_group_share", col, share, group_by=group, group=top))
                text += f" That is {share:.1f}% of the total."
            insights.append(
                Insight(
                    title=f"{col} by {group}",
                    text=text,
                    claims=claims,
                    chart=("bar", g.sort_values(ascending=False)),
                )
            )

    if date and nums:
        col = nums[0]
        monthly = _monthly_sums(df, date, col)
        if len(monthly) >= 2 and monthly.iloc[-2] != 0:
            prev, last = float(monthly.iloc[-2]), float(monthly.iloc[-1])
            change = (last - prev) / abs(prev) * 100
            insights.append(
                Insight(
                    title=f"{col}: latest month vs previous",
                    text=(
                        f"{col} moved {change:+.1f}% from {monthly.index[-2]} to {monthly.index[-1]}. "
                        "Check whether the latest month is complete before acting on this."
                    ),
                    claims=[Claim("period_change_pct", col, change, date_column=date)],
                    chart=("line", pd.Series(monthly.values, index=monthly.index.astype(str))),
                )
            )
    return insights
