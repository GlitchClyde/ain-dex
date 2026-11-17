import random

import pandas as pd
import pytest

from ain_dex import ain


@pytest.fixture()
def raw_df():
    random.seed(1)
    rows = []
    for i in range(120):
        rows.append(
            {
                "id": str(i + 1),
                "date": f"2025-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}",
                "region": random.choice(["North", "South", "East"]),
                "revenue": f"${random.randint(100, 900):,}.00",
            }
        )
    rows[5]["revenue"] = "N/A"
    rows[6]["revenue"] = "$99,999.00"  # outlier
    return pd.DataFrame(rows)


@pytest.fixture()
def pipeline(raw_df):
    clean = ain.prepare(raw_df)
    schema = ain.index_schema(clean)
    return raw_df, clean, schema
