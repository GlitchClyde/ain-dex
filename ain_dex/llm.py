"""Optional: let Claude phrase the executive summary.

Security/privacy design: only Dex-verified aggregate facts are sent to the API.
Raw rows never leave your machine. The returned text is then re-audited by Dex.
"""
from __future__ import annotations

import os

SYSTEM = (
    "You are Ain, a junior data analyst writing for non-technical executives. "
    "Use ONLY the facts provided. Never introduce a number that is not in the facts. "
    "Write 3-5 short bullets: what happened, why it matters, what to check next."
)


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def draft_narrative(facts: list[str]) -> str:
    import anthropic  # imported lazily so the app works without the SDK configured

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    resp = client.messages.create(
        model=os.getenv("AINDEX_MODEL", "claude-sonnet-5-5"),
        max_tokens=600,
        system=SYSTEM,
        messages=[{"role": "user", "content": "Verified facts:\n" + "\n".join(f"- {f}" for f in facts)}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
