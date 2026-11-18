from dataclasses import replace

from ain_dex import ain, dex
from ain_dex.models import Claim, Status


def test_all_ain_claims_verify(pipeline):
    raw, clean, schema = pipeline
    insights = ain.generate_insights(clean, schema)
    for vs in dex.audit(clean, insights, schema, raw):
        for v in vs:
            assert v.status is not Status.MISMATCH, v.message


def test_raw_reparse_catches_a_cleaning_bug(pipeline):
    """If cleaning corrupts a value, Ain's numbers are self-consistent but wrong. Only the raw re-parse sees it."""
    raw, clean, schema = pipeline
    corrupted = clean.copy()
    corrupted.loc[0, "revenue"] = 1_000_000.0  # simulated cleaning bug
    insights = ain.generate_insights(corrupted, schema)
    without_raw = dex.audit(corrupted, insights, schema)
    assert all(v.status is not Status.MISMATCH for vs in without_raw for v in vs)
    with_raw = dex.audit(corrupted, insights, schema, raw)
    assert any(v.status is Status.MISMATCH and "Cleaning disagreement" in v.message for vs in with_raw for v in vs)


def _dates_frame():
    import pandas as pd

    rows = []
    for i in range(30):
        day = i % 20 + 1
        rows.append({"d": f"2025-01-{day:02d}" if i % 2 else f"01/{day:02d}/2025", "v": "100"})
        rows.append({"d": f"2025-02-{day:02d}" if i % 2 else f"02/{day:02d}/2025", "v": "150"})
    return pd.DataFrame(rows)


def test_date_claims_are_reparsed_from_raw():
    raw = _dates_frame()
    clean = ain.prepare(raw)
    schema = ain.index_schema(clean)
    insights = ain.generate_insights(clean, schema)
    verdicts = [v for vs in dex.audit(clean, insights, schema, raw) for v in vs]
    period = [v for v in verdicts if v.claim.op == "period_change_pct"]
    assert period and all(v.status is not Status.MISMATCH for v in period)
    assert any("re-parsed from raw" in v.lineage for v in period)


def test_date_cleaning_bug_is_caught():
    import pandas as pd

    raw = _dates_frame()
    clean = ain.prepare(raw)
    schema = ain.index_schema(clean)
    corrupted = clean.copy()
    corrupted.loc[0, "d"] = pd.Timestamp("2025-02-05")  # a January row silently moved to February
    insights = ain.generate_insights(corrupted, schema)
    verdicts = [v for vs in dex.audit(corrupted, insights, schema, raw) for v in vs]
    assert any(v.claim.op == "period_change_pct" and v.status is Status.MISMATCH for v in verdicts)


def test_raw_date_parser_handles_mixed_formats():
    from ain_dex.dex import _parse_raw_date

    assert _parse_raw_date("2025-03-04").month == 3
    assert _parse_raw_date("03/04/2025").month == 3   # ambiguous: month-first
    assert _parse_raw_date("25/03/2025").month == 3   # day > 12 forces day-first
    assert _parse_raw_date("not a date") is None
    assert _parse_raw_date("2025-13-45") is None


def test_partial_latest_month_is_downgraded():
    import pandas as pd

    from ain_dex.models import Claim

    dates = pd.to_datetime(["2025-01-10", "2025-01-20", "2025-02-02", "2025-02-03"] * 10)
    df = pd.DataFrame({"d": dates, "v": [100.0, 100.0, 50.0, 50.0] * 10})
    # Jan = 2000, Feb = 1000 -> -50%
    v = dex.verify_claim(df, Claim("period_change_pct", "v", -50.0, date_column="d"))
    assert v.status is Status.ASSUMED and "incomplete" in v.message


def test_dex_blocks_a_tampered_claim(pipeline):
    _, clean, schema = pipeline
    insights = ain.generate_insights(clean, schema)
    good = insights[0].claims[0]
    bad = replace(good, value=good.value * 1.10)
    assert dex.verify_claim(clean, bad, schema).status is Status.MISMATCH


def test_dex_blocks_wrong_group(pipeline):
    _, clean, schema = pipeline
    claim = next(c for i in ain.generate_insights(clean, schema) for c in i.claims if c.op == "top_group_sum")
    assert dex.verify_claim(clean, replace(claim, group="Nowhere"), schema).status is Status.MISMATCH


def test_low_sample_flagged(pipeline):
    _, clean, schema = pipeline
    small = clean.head(10)
    claim = Claim("sum", "revenue", float(small["revenue"].sum()))
    assert dex.verify_claim(small, claim, schema).status is Status.LOW_SAMPLE


def test_health_check_finds_issues(pipeline):
    raw, clean, schema = pipeline
    msgs = " ".join(h.message for h in dex.health_check(raw, clean, schema))
    assert "[revenue]" in msgs and "missing" in msgs


def test_guardrail_blocks_missing_fields(pipeline):
    _, _, schema = pipeline
    g = dex.check_scope("What is our customer acquisition cost?", schema)
    assert not g.supported and "DEX GUARDRAIL" in g.message
    assert not dex.check_scope("What is our profit margin?", schema).supported
    assert dex.check_scope("Which region has the highest revenue?", schema).supported


def test_narrative_audit_flags_invented_numbers():
    allowed = [1234567.89, 42.5]
    assert dex.audit_narrative("Revenue reached $1.23M, up 42.5%.", allowed) == []
    assert dex.audit_narrative("Revenue grew 77.7% last quarter.", allowed) == ["77.7%"]
    assert dex.audit_narrative("In 2025 we saw 3 insights.", allowed) == []
