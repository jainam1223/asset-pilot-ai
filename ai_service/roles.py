"""
roles.py — table-level scope per user role.

Three roles exist in the `user` table: employee, manager, it_admin.

- it_admin sees all 8 tables, no restriction.
- employee and manager are both confined to the 3 catalog tables:
  user, item, item_category. Devices, categories, and who currently
  holds what — nothing about requests, support tickets, extensions,
  handovers, or the audit log. Those questions get refused with a
  PermissionError, same as any other out-of-scope table.

No row-level filtering is needed in this baseline since employee and
manager scope never touches a table with per-user ownership.
"""

from __future__ import annotations

CATALOG_TABLES = {"user", "item_category", "item"}

ALL_TABLES = CATALOG_TABLES | {
    "request",
    "extension_request",
    "handover_request",
    "support_request",
    "device_log",
}

TABLE_SCOPE: dict[str, set[str]] = {
    "it_admin": ALL_TABLES,
    "employee": CATALOG_TABLES,
    "manager": CATALOG_TABLES,
}

VALID_ROLES = set(TABLE_SCOPE)
