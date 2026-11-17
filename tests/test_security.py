import io
import zipfile

import pandas as pd
import pytest

from ain_dex import security


def test_rejects_disallowed_extension():
    with pytest.raises(security.UploadRejected):
        security.validate_upload("evil.exe", b"MZ...")


def test_rejects_macro_workbook_extension():
    with pytest.raises(security.UploadRejected):
        security.validate_upload("book.xlsm", b"PK\x03\x04")


def test_rejects_oversized_file():
    with pytest.raises(security.UploadRejected):
        security.validate_upload("a.csv", b"a" * (security.MAX_UPLOAD_BYTES + 1))


def test_rejects_fake_xlsx():
    with pytest.raises(security.UploadRejected):
        security.validate_upload("a.xlsx", b"just text")


def test_rejects_binary_csv():
    with pytest.raises(security.UploadRejected):
        security.validate_upload("a.csv", b"a,b\n\x00\x01")


def test_rejects_zip_bomb_ratio():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/sheet.xml", b"0" * (5 * 1024 * 1024))
    with pytest.raises(security.UploadRejected):
        security.validate_upload("a.xlsx", buf.getvalue())


def test_path_traversal_is_stripped():
    assert security.validate_upload("../../etc/x.csv", b"a,b\n1,2\n") == "x.csv"


def test_loads_csv_as_strings():
    df = security.load_dataframe("a.csv", b"a,b\n1,$2.00\n")
    assert list(df.columns) == ["a", "b"]


def test_formula_injection_is_neutralised():
    df = pd.DataFrame({"x": ["=cmd|' /C calc'!A0", "+1", "-2", "@SUM(A1)", "safe"]})
    out = security.sanitize_for_export(df)
    assert out["x"].tolist()[:4] == ["'=cmd|' /C calc'!A0", "'+1", "'-2", "'@SUM(A1)"]
    assert out["x"].iloc[4] == "safe"
