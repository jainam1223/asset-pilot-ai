"""
synthesis.py — second-stage LLM call that turns query rows into a
human-facing Markdown answer.

The end user never sees SQL, table names, or column names — this is
the only thing rendered in the chat UI. Kept separate from SQL
generation so each prompt can be tuned independently.
"""

from __future__ import annotations

from ai_service.prompts import build_answer_messages
from ai_service.providers import Provider, complete_text

MAX_RESULT_ROWS_FOR_ANSWER = 50


async def synthesize_answer(
    question: str, rows: list[dict], *, chain: list[Provider], caveat: str | None = None
) -> tuple[str, str]:
    """Turn query rows into a Markdown answer for `question`.

    `caveat`, when given, is a note from the SQL-generation stage about
    part of the request that was declined (e.g. a mixed read+write
    message) — it gets folded into the answer so the user isn't left
    thinking the declined part silently happened or silently failed.

    Returns (markdown_answer, provider_name_used).
    """
    messages = build_answer_messages(question, rows, max_rows=MAX_RESULT_ROWS_FOR_ANSWER, caveat=caveat)
    text, provider_name = await complete_text(messages, chain=chain)
    return text.strip(), provider_name
