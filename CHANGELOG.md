# Changelog

## 0.3.0
- New Ask tab: plain-English questions answered by Ain and verified by Dex against raw cells.
- Optional Claude interpretation limited to a validated JSON query plan (column names and types only are sent).
- Dex supports a `count` operation; guardrail also blocks totals of price/rate columns.

## 0.2.0
- Dex re-parses raw cells (numbers, labels and dates) with its own parser, so cleaning bugs cannot hide.
- Ain skips totals for price/rate-like columns.
- Month-over-month claims are downgraded when the latest month is incomplete.

## 0.1.0
- Initial release: Ain (schema index, insights), Dex (recompute, health check, scope guardrail, narrative audit), upload hardening, Streamlit UI, CI.
