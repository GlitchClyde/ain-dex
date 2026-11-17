"""Shared data structures for Ain and Dex."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    VERIFIED = "Verified"
    ASSUMED = "Assumed/Imputed"
    LOW_SAMPLE = "Low Sample Size"
    MISMATCH = "Blocked: Mismatch"


SEVERITY = {
    Status.VERIFIED: 0,
    Status.ASSUMED: 1,
    Status.LOW_SAMPLE: 2,
    Status.MISMATCH: 3,
}


@dataclass(frozen=True)
class Claim:
    """A single numeric assertion Ain wants to make about the data."""

    op: str  # sum | mean | median | outlier_count | top_group_sum | top_group_share | period_change_pct
    column: str
    value: float
    group_by: str | None = None
    group: str | None = None
    date_column: str | None = None


@dataclass
class ColumnInfo:
    name: str
    kind: str  # numeric | datetime | categorical | text | boolean | id
    nulls: int
    unique: int
    null_pct: float


@dataclass
class SchemaIndex:
    rows: int
    columns: list[ColumnInfo]

    def of_kind(self, kind: str) -> list[ColumnInfo]:
        return [c for c in self.columns if c.kind == kind]


@dataclass
class Insight:
    title: str
    text: str
    claims: list[Claim]
    chart: tuple[str, Any] | None = None  # ("bar" | "line", pandas.Series)


@dataclass
class Verdict:
    claim: Claim
    status: Status
    message: str
    lineage: str
    recomputed: float | None = None


@dataclass
class HealthIssue:
    severity: str  # info | warning | critical
    message: str


@dataclass
class GuardrailResult:
    supported: bool
    message: str
    missing: list[str] = field(default_factory=list)
