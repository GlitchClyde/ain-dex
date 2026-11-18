"""Ask Ain a question in plain English.

The language model (if used) never calculates anything. It only proposes a small
query plan, which is validated against a strict allowlist and the real schema.
Ain computes the number with pandas, then Dex re-computes it from the raw cells.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from . import ain, dex
from .models import Claim, SchemaIndex, Status, Verdict
from .security import safe_name

ALLOWED_OPS = {"sum", "mean", "median", "count", "outlier_count", "top_group_sum", "top_group_share", "period_change_pct"}
ALLOWED_KEYS = {"op", "column", "group_by", "date_column"}
MAX_QUESTION_CHARS = 500
HELP = (
    "I can answer: totals, averages, medians, counts, outliers, which group is highest, "
    "a group's share, and month-over-month change. Try: "
)


class PlanRejected(ValueError):
    """The query plan failed validation."""


@dataclass(frozen=True)
class QueryPlan:
    op: str
    column: str
    group_by: str | None = None
    date_column: str | None = None


@dataclass
class Answer:
    question: str
    supported: bool
    text: str
    plan: QueryPlan | None = None
    claim: Claim | None = None
    verdict: Verdict | None = None
    source: str = "rules"  # rules | claude


# --------------------------------------------------------------------------- #
# Plan validation (the security boundary)
# --------------------------------------------------------------------------- #
def _resolve(name, schema: SchemaIndex, kind: str) -> str:
    if not isinstance(name, str):
        raise PlanRejected(f"Expected a {kind} column name.")
    lookup = {}
    for c in schema.columns:
        if c.kind == kind:
            lookup[c.name.lower()] = c.name
            lookup[safe_name(c.name).lower()] = c.name
    found = lookup.get(name.strip().lower())
    if found is None:
        raise PlanRejected(f"'{name[:40]}' is not a {kind} column in this dataset.")
    return found


def validate_plan(raw: dict, schema: SchemaIndex) -> QueryPlan:
    if not isinstance(raw, dict):
        raise PlanRejected("Plan must be an object.")
    extra = set(raw) - ALLOWED_KEYS
    if extra:
        raise PlanRejected(f"Unexpected keys in plan: {sorted(extra)}.")
    op = raw.get("op")
    if op not in ALLOWED_OPS:
        raise PlanRejected("That question needs an operation Ain does not support yet.")
    column = _resolve(raw.get("column"), schema, "numeric")
    group_by = date_column = None
    if op in {"top_group_sum", "top_group_share"}:
        group_by = _resolve(raw.get("group_by"), schema, "categorical")
    if op == "period_change_pct":
        date_column = _resolve(raw.get("date_column"), schema, "datetime")
    if op in {"sum", "top_group_sum", "top_group_share"} and not ain.is_additive(column):
        raise PlanRejected(
            f"[DEX GUARDRAIL: A total or share of [{column}] is not meaningful because it looks like a price or rate. "
            "Ask for the average or median instead.]"
        )
    return QueryPlan(op, column, group_by, date_column)


# --------------------------------------------------------------------------- #
# Rule-based interpreter (works offline, no API key)
# --------------------------------------------------------------------------- #
_OPS = [
    (r"outlier|anomal", "outlier_count"),
    (r"\bchange|growth|grew|month over month|vs\.? last month|versus", "period_change_pct"),
    (r"\bshare\b|percent|proportion|%|\bpct\b", "top_group_share"),
    (r"\bhighest|\btop\b|\bbest\b|\bmost\b|largest|biggest|leading|\bwhich\b", "top_group_sum"),
    (r"\bmedian", "median"),
    (r"\baverage|\bmean\b|\bavg\b", "mean"),
    (r"how many|\bcount\b|number of", "count"),
    (r"\btotal|\bsum\b|add up", "sum"),
]


def _norm(s: str) -> str:
    return re.sub(r"[_\s]+", " ", s.lower()).strip()


def _first_mentioned(question: str, names: list[str]) -> str | None:
    q = _norm(question)
    best: tuple[int, str] | None = None
    for n in names:
        m = re.search(rf"\b{re.escape(_norm(n))}s?\b", q)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), n)
    return best[1] if best else None


def parse_rule_based(question: str, schema: SchemaIndex) -> dict | None:
    ql = question.lower()
    op = next((o for pat, o in _OPS if re.search(pat, ql)), None)
    if op is None:
        return None
    nums = [c.name for c in schema.of_kind("numeric")]
    cats = [c.name for c in schema.of_kind("categorical")]
    dates = [c.name for c in schema.of_kind("datetime")]
    col = _first_mentioned(question, nums) or (nums[0] if len(nums) == 1 else None)
    if col is None:
        return None
    grp = _first_mentioned(question, cats)
    if op in {"top_group_sum", "top_group_share"}:
        return {"op": op, "column": col, "group_by": grp} if grp else None
    if op == "period_change_pct":
        return {"op": op, "column": col, "date_column": dates[0]} if dates else None
    if op in {"sum", "mean", "median", "count"} and grp:
        raise PlanRejected(
            f"Per-group breakdowns are not supported yet. Try: which {grp} has the highest {col}?"
        )
    return {"op": op, "column": col}


# --------------------------------------------------------------------------- #
# Answering
# --------------------------------------------------------------------------- #
def _describe(c: Claim) -> str:
    fmt = ain.fmt
    if c.op == "sum":
        return f"Total {c.column}: {fmt(c.value)}"
    if c.op == "mean":
        return f"Average {c.column}: {fmt(c.value)}"
    if c.op == "median":
        return f"Median {c.column}: {fmt(c.value)}"
    if c.op == "count":
        return f"{int(c.value):,} records have a value for {c.column}"
    if c.op == "outlier_count":
        return f"{int(c.value):,} outlier value(s) in {c.column} (outside 1.5x the interquartile range)"
    if c.op == "top_group_sum":
        return f"'{c.group}' has the highest {c.column} across {c.group_by}: {fmt(c.value)}"
    if c.op == "top_group_share":
        return f"'{c.group}' accounts for {c.value:.1f}% of {c.column} across {c.group_by}"
    return f"{c.column} changed {c.value:+.1f}% between the last two months of {c.date_column}"


def suggestions(schema: SchemaIndex) -> list[str]:
    nums = [c.name for c in schema.of_kind("numeric") if ain.is_additive(c.name)]
    cats = [c.name for c in schema.of_kind("categorical")]
    out: list[str] = []
    if nums:
        out.append(f"What is the total {nums[0]}?")
        out.append(f"How many outliers are in {nums[0]}?")
    if nums and cats:
        out.append(f"Which {cats[0]} has the highest {nums[0]}?")
        out.append(f"What share of {nums[0]} comes from the top {cats[0]}?")
    if nums and schema.of_kind("datetime"):
        out.append(f"How did {nums[0]} change versus last month?")
    return out[:5]


def ask(
    question: str,
    df: pd.DataFrame,
    raw: pd.DataFrame | None,
    schema: SchemaIndex,
    planner: Callable[[str, SchemaIndex], dict] | None = None,
) -> Answer:
    q = (question or "").strip()
    if not q:
        return Answer(q, False, "Type a question about the data.")
    if len(q) > MAX_QUESTION_CHARS:
        return Answer(q[:80], False, f"Questions are limited to {MAX_QUESTION_CHARS} characters.")

    scope = dex.check_scope(q, schema)
    if not scope.supported:
        return Answer(q, False, scope.message)

    source = "rules"
    try:
        plan_dict = None
        if planner is not None:
            try:
                plan_dict = planner(q, schema)
                source = "claude"
            except Exception:  # network/auth/JSON problems: quietly fall back to rules
                plan_dict = None
        if plan_dict is None:
            source = "rules"
            plan_dict = parse_rule_based(q, schema)
        if plan_dict is None:
            return Answer(q, False, HELP + " / ".join(suggestions(schema)))
        plan = validate_plan(plan_dict, schema)
    except PlanRejected as exc:
        return Answer(q, False, f"Dex rejected this request: {exc}")

    claim = ain.compute_claim(df, plan.op, plan.column, plan.group_by, plan.date_column)
    if claim is None:
        return Answer(q, False, "There is not enough valid data to compute that.", plan=plan, source=source)

    verdict = dex.verify_claim(df, claim, schema, raw)
    if verdict.status is Status.MISMATCH:
        return Answer(q, False, f"Blocked by Dex: {verdict.message}", plan, claim, verdict, source)
    return Answer(q, True, _describe(claim), plan, claim, verdict, source)
