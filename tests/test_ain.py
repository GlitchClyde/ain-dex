import pandas as pd

from ain_dex import ain


def test_coerce_numeric_handles_currency_and_parentheses():
    s = pd.Series(["$1,200.50", "(300)", "15%", "N/A", None])
    s = s.where(~s.isin(["N/A"]), None)
    out = ain.coerce_numeric(s)
    assert out.iloc[0] == 1200.50 and out.iloc[1] == -300 and out.iloc[2] == 15


def test_coerce_numeric_rejects_text():
    assert ain.coerce_numeric(pd.Series(["North", "South", "East"])) is None


def test_prepare_and_schema_kinds(pipeline):
    _, clean, schema = pipeline
    kinds = {c.name: c.kind for c in schema.columns}
    assert kinds["id"] == "id"
    assert kinds["date"] == "datetime"
    assert kinds["region"] == "categorical"
    assert kinds["revenue"] == "numeric"
    assert schema.columns[-1].nulls == 1


def test_words_like_may_are_not_dates():
    assert ain.coerce_datetime(pd.Series(["May", "June", "July"])) is None


def test_insights_generated(pipeline):
    _, clean, schema = pipeline
    titles = [i.title for i in ain.generate_insights(clean, schema)]
    assert "revenue: overall profile" in titles
    assert "revenue by region" in titles
    assert any("outliers" in t for t in titles)
