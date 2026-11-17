from dataclasses import replace

from ain_dex import ain, dex
from ain_dex.models import Claim, Status


def test_all_ain_claims_verify(pipeline):
    _, clean, schema = pipeline
    insights = ain.generate_insights(clean, schema)
    for vs in dex.audit(clean, insights, schema):
        for v in vs:
            assert v.status is not Status.MISMATCH, v.message


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
