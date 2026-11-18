# Ain.dex

**Ain analyses. Dex audits.** A spreadsheet analysis tool where every number must survive an independent check before it is called verified.

- **Ain** (junior analyst): ingests a CSV/XLSX, indexes the schema, cleans messy types (`$1,200.50`, `(300)`, mixed date formats), drafts insights, and answers plain-English questions.
- **Dex** (auditor): re-computes every claim with a *separate* pure-Python implementation and re-parses the raw cells itself (so a cleaning bug cannot hide), blocks mismatches, flags data-quality problems, refuses questions the data cannot answer, and stamps each result with lineage (`Col [revenue], n=291 rows, 12 null(s) excluded`).

Status badges: ✅ Verified · 🟡 Assumed/Imputed · 🟠 Low Sample Size · ⛔ Blocked (mismatch)

## Quick start (Windows, VS Code)

```powershell
git clone https://github.com/YOUR-USERNAME/ain-dex.git
cd ain-dex
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements-dev.txt
pytest -q                          # 52 tests should pass
streamlit run app.py
```

Then open the folder in VS Code (`code .`), pick the `.venv` interpreter (Ctrl+Shift+P → *Python: Select Interpreter*), and use **Run and Debug → Ain.dex (Streamlit)** or the Testing panel.

Optional AI summaries: copy `.env.example` to `.env` and add `ANTHROPIC_API_KEY`. Without it everything else works.

## Architecture

```
app.py                Streamlit UI (Ain Workspace | Dex Audit | Ask | Export)
ain_dex/
  security.py         upload validation, zip-bomb guard, CSV/Excel formula-injection sanitising
  ain.py              cleaning, type inference, schema index, insight generation (pandas)
  ask.py              plain-English Q&A: rule-based or Claude-planned, strictly validated query plans
  dex.py              independent recomputation, health check, scope guardrail, narrative audit
  llm.py              optional Claude-written summary (verified aggregates only)
  report.py           Markdown report with verification stamps
tests/                52 tests: security, Ain, Dex, Q&A, and a UI smoke test
```

Flow: **upload → validate → clean → index → Ain drafts claims → Dex recomputes → badge or block → export.**

## Security design (the cybersecurity angle)

| Threat | Control |
|---|---|
| Malicious file types / macros | Extension allowlist; `.xlsm` and `.xls` rejected; macro (`vbaProject.bin`) check |
| Zip bombs in `.xlsx` | Uncompressed-size and compression-ratio limits |
| Resource exhaustion | 10 MB, 200k row, 500 column caps |
| Path traversal in filenames | `os.path.basename` on every upload |
| CSV / Excel formula injection | Exported cells starting with `= + - @` are neutralised |
| Data leakage to an LLM | Only Dex-verified aggregates are sent, never raw rows |
| Secret exposure | API key from environment / `.env` (git-ignored), never logged or echoed |
| Hallucinated numbers in AI text | Dex flags any number in a narrative that matches no verified value |
| LLM tricked into running code or inventing columns | The model only proposes a JSON plan. Ain validates it against an operation allowlist and the real schema, rejects unknown keys, and never executes model output |
| Prompt injection via column names | Names are sanitised and length-capped before reaching the prompt, and the returned plan is validated regardless |
| Supply chain / code issues | CI runs Bandit and pip-audit |

## Known limitations (good first issues)

1. Ambiguous dates such as `03/04/2025` are read month-first by both Ain and Dex, so a day-first dataset would be misread by both. Add a per-file date-format setting.
2. The "price or rate" column rule and the "incomplete month" rule are name- and threshold-based heuristics. Make them configurable.
3. Scope guardrail uses keyword rules, not semantic matching.
4. Q&A supports one calculation per question. Per-group tables ("total revenue by region"), filters ("in Q3") and multi-step questions are not supported yet.

## Roadmap ideas

Group-by and filter questions · Chat history · SQL/DB connectors · Dex confidence scores using statistical tests · audit log export · Docker image · deployment on Streamlit Community Cloud.

## Add to your portfolio

Pin the repo, add a screenshot to this README, and describe it in one line: *"Built a spreadsheet analysis tool with an independent verification layer that recomputes every metric, with upload hardening against zip bombs and formula injection, tested via CI on Windows."*

## License

MIT
