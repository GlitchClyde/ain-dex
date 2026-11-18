"""Upload validation and export sanitisation.

Treats every uploaded file as untrusted input.
"""
from __future__ import annotations

import io
import os
import re
import zipfile

import pandas as pd

ALLOWED_EXTENSIONS = {".csv", ".xlsx"}  # .xlsm (macros) and .xls are rejected on purpose
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # zip-bomb guard for .xlsx
MAX_COMPRESSION_RATIO = 100
MAX_ROWS = 200_000
MAX_COLUMNS = 500
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class UploadRejected(ValueError):
    """Raised when an uploaded file fails a security or size check."""


def validate_upload(filename: str, data: bytes) -> str:
    """Validate an upload and return the safe base filename."""
    name = os.path.basename(filename or "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected(f"File type '{ext or 'unknown'}' is not allowed. Use .csv or .xlsx.")
    if len(data) == 0:
        raise UploadRejected("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")

    if ext == ".xlsx":
        if not data.startswith(b"PK\x03\x04"):
            raise UploadRejected("File content does not match the .xlsx format.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                total = sum(i.file_size for i in zf.infolist())
                if total > MAX_UNCOMPRESSED_BYTES or total > len(data) * MAX_COMPRESSION_RATIO:
                    raise UploadRejected("Workbook expands to a suspicious size and was rejected.")
                if any(n.lower().endswith("vbaproject.bin") for n in zf.namelist()):
                    raise UploadRejected("Workbooks containing macros are not allowed.")
        except zipfile.BadZipFile as exc:
            raise UploadRejected("The .xlsx file is corrupted.") from exc
    else:
        if b"\x00" in data[:8192]:
            raise UploadRejected("File content does not look like a text CSV.")
    return name


def load_dataframe(filename: str, data: bytes) -> pd.DataFrame:
    """Validate then parse an upload into a raw DataFrame (CSV values stay as strings)."""
    name = validate_upload(filename, data)
    try:
        if name.lower().endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(data), engine="openpyxl")
        else:
            df = None
            for enc in ("utf-8-sig", "latin-1"):
                try:
                    df = pd.read_csv(
                        io.BytesIO(data),
                        encoding=enc,
                        dtype=str,
                        keep_default_na=False,
                        nrows=MAX_ROWS + 1,
                    )
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                raise UploadRejected("Could not decode the CSV file.")
    except UploadRejected:
        raise
    except Exception as exc:  # parser errors: report cleanly, never echo file contents
        raise UploadRejected(f"Could not parse the file: {type(exc).__name__}.") from exc

    if len(df) > MAX_ROWS:
        raise UploadRejected(f"Too many rows (limit {MAX_ROWS:,}).")
    if df.shape[1] > MAX_COLUMNS:
        raise UploadRejected(f"Too many columns (limit {MAX_COLUMNS}).")
    if df.empty:
        raise UploadRejected("The file has no data rows.")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def safe_name(name: str, limit: int = 60) -> str:
    """Make an untrusted column name safe to place in an LLM prompt.

    Column names come from the uploaded file, so they could carry injected instructions.
    Strip anything that is not a plain name character and cap the length.
    """
    cleaned = re.sub(r"[^\w .\-/%()$#]", "", str(name))
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def sanitize_cell(value):
    """Neutralise spreadsheet formula injection (CSV/Excel 'DDE' style attacks)."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def sanitize_for_export(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy safe to write to CSV/XLSX and open in Excel."""
    out = df.copy()
    out.columns = [sanitize_cell(str(c)) for c in out.columns]
    for col in out.columns:
        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(sanitize_cell)
    return out
