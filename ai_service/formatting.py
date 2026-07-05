"""formatting.py — debug/eval rendering helpers for SQL results and row samples.

Not part of the end-user-facing path — pipeline.ask() only falls back
to format_rows() when answer synthesis itself fails, and the CLI uses
render() for --show-sql debug output.
"""

from __future__ import annotations

from textwrap import indent

from ai_service.models import SQLResponse


def render(result: SQLResponse) -> str:
    bar = "─" * 60
    if result.sql is None:
        return f"\n{result.explanation}\n"
    return (
        f"\n{bar}\n"
        f"SQL\n"
        f"{bar}\n"
        f"{indent(result.sql, '  ')}\n"
        f"\n{bar}\n"
        f"EXPLANATION\n"
        f"{bar}\n"
        f"{result.explanation}\n"
    )


def _humanize_key(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def format_rows(rows: list[dict], sample_size: int = 5) -> str:
    """Best-effort plain-language rendering used only when the
    answer-synthesis LLM call itself fails (every provider down/cooling
    off) — pipeline.ask()'s last resort before showing the user
    something. Since there's no LLM available to phrase this well, it
    won't read as naturally as a real synthesized answer, but it must
    never show a raw SQL column/alias name or look like a database
    table dump — that's the whole reason this exists instead of just
    printing the rows.
    """
    if not rows:
        return "I didn't find anything matching that."

    if len(rows) == 1 and len(rows[0]) == 1:
        value = next(iter(rows[0].values()))
        return f"The result is **{value}**."

    sample = rows[:sample_size]
    lines = [
        "- " + ", ".join(f"{_humanize_key(k)}: {v}" for k, v in row.items() if v not in (None, ""))
        for row in sample
    ]
    out = "\n".join(lines)
    if len(rows) > sample_size:
        out += f"\n...and {len(rows) - sample_size} more."
    return out
