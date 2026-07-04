"""
sql_check.py — post-generation SQL validation.

Two guarantees enforced here, both by raising a real exception rather
than returning a flag the caller might ignore:

1. The SQL is SELECT-only — no DML/DDL, ever, regardless of role.
2. The SQL only references tables the caller's role is allowed to see
   (roles.TABLE_SCOPE).

These are shallow, regex-level checks — not a real SQL parser. They
are a safety net behind the system prompt, which is the primary
defense: the prompt only shows the model tables the role can see, and
instructs it to refuse destructive requests outright. This module
catches the case where the model ignores that anyway.
"""

from __future__ import annotations

import re

from ai_service.roles import TABLE_SCOPE

BANNED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "CREATE", "RENAME",
}

# All table names that exist anywhere in the schema — used to find
# which ones a query actually references.
KNOWN_TABLES = {
    "user", "item_category", "item", "request", "extension_request",
    "handover_request", "support_request", "device_log",
}


class DisallowedOperationError(PermissionError):
    """Raised when generated SQL contains a non-SELECT keyword."""


class OutOfScopeTableError(PermissionError):
    """Raised when generated SQL references a table outside the role's scope."""


def _referenced_tables(sql: str) -> set[str]:
    return {t for t in KNOWN_TABLES if re.search(rf'\b"?{re.escape(t)}"?\b', sql, re.IGNORECASE)}


def check_shape(sql: str, *, role: str) -> None:
    """Validate generated SQL against the hard rules for `role`.

    Raises DisallowedOperationError or OutOfScopeTableError on
    violation. Returns None on success — no result object to inspect,
    callers just call it and continue if it doesn't raise.
    """
    upper = sql.upper()
    for kw in BANNED_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            raise DisallowedOperationError(
                f"Generated SQL contains a disallowed operation ({kw}). "
                "Only SELECT statements are permitted."
            )

    allowed = TABLE_SCOPE.get(role)
    if allowed is None:
        raise ValueError(f"Unknown role: {role!r}")

    used = _referenced_tables(sql)
    out_of_scope = used - allowed
    if out_of_scope:
        raise OutOfScopeTableError(
            f"This question requires access to {sorted(out_of_scope)}, "
            f"which is outside the '{role}' role's scope. "
            f"'{role}' can only query: {sorted(allowed)}."
        )
