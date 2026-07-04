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


def format_rows(rows: list[dict], sample_size: int = 5) -> str:
    if not rows:
        return "(0 rows)"

    columns = list(rows[0].keys())
    sample = rows[:sample_size]
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in sample)) for c in columns}

    header = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    body = "\n".join(
        " | ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns)
        for r in sample
    )

    out = f"{header}\n{sep}\n{body}"
    if len(rows) > sample_size:
        out += f"\n({len(rows)} rows total, {sample_size} shown)"
    else:
        out += f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})"
    return out
