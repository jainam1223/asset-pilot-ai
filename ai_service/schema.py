"""
schema.py — DBML loading and role-based table slicing.

Uses the trimmed DBML (fewer tokens, same tables/columns as the full
schema) as the sole source of schema context sent to the LLM — the
full DBML is a dev-only artifact used for schema comparison, not
needed at runtime. The file lives at the repo root (a sibling of this
package). load_schema() reads it; slice_schema_for_role() then drops
any Table block the requesting role isn't allowed to see.
"""

from __future__ import annotations

from pathlib import Path

from ai_service.roles import TABLE_SCOPE

SCHEMA_FILENAME = "schema_llm_context 1.dbml"

# The DBML file lives at the repo root, one level above this package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _REPO_ROOT / SCHEMA_FILENAME


def load_schema() -> str:
    """Read the trimmed DBML at the repo root and strip comments and
    the Project {} wrapper block."""
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {_SCHEMA_PATH}")

    raw = _SCHEMA_PATH.read_text(encoding="utf-8")
    lines = [
        line
        for line in raw.splitlines()
        if not line.strip().startswith("//") and not line.strip().startswith("Project ")
    ]
    return "\n".join(lines).strip()


def slice_schema_for_role(schema: str, role: str) -> str:
    """Return only the Table {...} blocks whose name is in the role's
    scope, plus every enum definition (enums are small and a dropped
    table might still share an enum type worth keeping context for).

    This is a simple brace-matching pass, not a real DBML parser — the
    DBML files here are static and consistently formatted so this is
    sufficient. If a table isn't in scope, drop the whole block.
    """
    allowed = TABLE_SCOPE.get(role)
    if allowed is None:
        raise ValueError(f"Unknown role: {role!r}")

    lines = schema.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("Table "):
            table_name = stripped.split()[1]
            depth = line.count("{") - line.count("}")
            block = [line]
            i += 1
            while depth > 0 and i < len(lines):
                block.append(lines[i])
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            if table_name in allowed:
                out.extend(block)
            continue  # already advanced i past the block

        out.append(line)
        i += 1

    return "\n".join(out).strip()
