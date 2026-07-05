"""
db.py — Postgres connection pool, role lookup, and read-only SELECT execution.

Fully async (psycopg.AsyncConnection via psycopg_pool.AsyncConnectionPool)
so this drops straight into a FastAPI route without blocking the event
loop. The AI service does not own the schema (the backend team does)
and never writes to it — every connection handed out here is read-only
by session default as a second wall behind the SQL-shape checks in
sql_check.py.

A process-wide pool replaces the old per-request connect/close: opening
a fresh connection to an Azure-hosted Postgres (TLS handshake + auth,
on top of TCP) on every single request was pure overhead the pool
eliminates by keeping a handful of already-authenticated connections
warm and reusing them.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import psycopg
from dotenv import load_dotenv
from loguru import logger
from psycopg_pool import AsyncConnectionPool, PoolTimeout

load_dotenv()

# Without these, a slow/unreachable Postgres endpoint hangs the calling
# request indefinitely (observed: a 2-minute stall during testing with
# no timeout configured). CONNECT_TIMEOUT bounds how long a checkout
# waits for a connection to become available; STATEMENT_TIMEOUT_MS
# bounds how long any single query can run once connected.
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 15_000

# Small on purpose: this is a low-concurrency hackathon demo, not a
# service under real load. min_size keeps at least one connection warm
# so the common case skips the handshake entirely; max_size just caps
# how many the pool will ever open concurrently.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5

# Circuit breaker: once a checkout fails, stop trying fresh checkouts
# for this long and fail fast with the transient message instead.
# Without this, a real Postgres outage means every single concurrent
# request independently waits out the full pool checkout timeout — this
# mirrors the cooldown pattern in ai_service/providers.py, just for the
# one DB target instead of a chain of LLM providers.
DB_COOLDOWN_SECONDS = 30.0

# Module-level, not per-request: this state is shared across every
# caller in the process, which is the point — one failed checkout
# should stop the *next* caller from immediately re-trying too.
_cooldown_until = 0.0

_pool: AsyncConnectionPool | None = None

# Guards pool creation only (a one-time event per process) — not every
# checkout. Without this, N concurrent first-callers would each try to
# construct their own AsyncConnectionPool.
_pool_lock = asyncio.Lock()


def _db_cooling_down() -> bool:
    return time.monotonic() < _cooldown_until


def _db_cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - time.monotonic())


def _start_db_cooldown() -> None:
    global _cooldown_until
    _cooldown_until = time.monotonic() + DB_COOLDOWN_SECONDS


def _clear_db_cooldown() -> None:
    global _cooldown_until
    _cooldown_until = 0.0


class UnknownUserError(PermissionError):
    """Raised when a lookup email doesn't match any row in `user`."""


class QueryExecutionError(RuntimeError):
    """Wraps a psycopg error raised while executing a validated SELECT."""


class DatabaseConnectionError(RuntimeError):
    """Wraps a connection-level failure (unreachable, timed out, refused),
    including a fast-fail while the circuit breaker is cooling down."""


def _raise_cooling_down() -> None:
    remaining = _db_cooldown_remaining()
    logger.debug(f"DB connection skipped — cooling down for {remaining:.0f}s more")
    raise DatabaseConnectionError(
        f"Skipping connection attempt — database was unreachable "
        f"{DB_COOLDOWN_SECONDS - remaining:.0f}s ago, retrying in {remaining:.0f}s"
    )


async def _configure_connection(conn: psycopg.AsyncConnection) -> None:
    """Run once per physical connection, when the pool creates it — not
    on every checkout. Read-only and the statement timeout become
    session-level defaults for the lifetime of this physical
    connection, so a normal request just checks out a connection and
    queries it, with no per-request setup statements."""
    await conn.set_autocommit(True)
    await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    await conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")


async def _get_pool() -> AsyncConnectionPool:
    """Lazily create the single process-wide pool on first use.

    Normally this only runs once, at app startup, via warm_pool() below
    — see its docstring for why. This lazy path just makes get_connection()
    self-sufficient (e.g. under a bare pytest that never calls warm_pool()),
    without changing steady-state behavior.
    """
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            url = os.getenv("POSTGRES_URL")
            if not url:
                raise RuntimeError("POSTGRES_URL is not set in .env")
            pool = AsyncConnectionPool(
                url,
                min_size=POOL_MIN_SIZE,
                max_size=POOL_MAX_SIZE,
                timeout=CONNECT_TIMEOUT_SECONDS,
                configure=_configure_connection,
                open=False,
            )
            # wait=False: don't block whoever triggered pool creation on a
            # DB that might be down — the pool fills connections in the
            # background, and a checkout below still bounds its own wait.
            await pool.open(wait=False)
            _pool = pool
    return _pool


async def warm_pool() -> None:
    """Pre-create POOL_MIN_SIZE connections at app startup instead of
    lazily on the first real request.

    Without this, the pool exists (via _get_pool()) but is empty until
    something checks it out — so the very first user request after a
    cold start still pays the full connect+TLS+auth cost, the exact
    overhead the pool was added to remove. Calling this from the
    FastAPI lifespan moves that one-time cost to boot instead of onto
    whoever's request happens to arrive first.

    Bounded by CONNECT_TIMEOUT_SECONDS and never raises — if the DB is
    down at startup, this just logs and leaves the pool empty; normal
    per-request checkout (and its circuit breaker) still applies as
    before, so a DB outage at boot doesn't stop the app from starting.
    """
    pool = await _get_pool()
    try:
        await pool.wait(timeout=CONNECT_TIMEOUT_SECONDS)
        logger.info(f"DB pool warmed: {POOL_MIN_SIZE} connection(s) ready")
    except PoolTimeout as exc:
        logger.warning(f"DB pool did not warm up within {CONNECT_TIMEOUT_SECONDS}s (DB may be down): {exc}")


async def close_pool() -> None:
    """Close the pool and every connection it holds. Call once at app
    shutdown so nothing is left dangling when the process exits."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """Check out a pooled, read-only connection.

    If a recent checkout failed, this fails immediately (no pool wait
    at all) until DB_COOLDOWN_SECONDS has passed — see the circuit
    breaker constants above. Otherwise, waits up to
    CONNECT_TIMEOUT_SECONDS for the pool to hand back a connection
    (instant if one is already idle in the pool, which is the common
    case once it has warmed up).
    """
    if _db_cooling_down():
        _raise_cooling_down()

    pool = await _get_pool()
    try:
        async with pool.connection(timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            _clear_db_cooldown()  # a successful checkout means the DB is up
            yield conn
    except (PoolTimeout, psycopg.OperationalError, TimeoutError) as exc:
        logger.error(f"Could not get a Postgres connection within {CONNECT_TIMEOUT_SECONDS}s: {exc}")
        _start_db_cooldown()
        raise DatabaseConnectionError(str(exc)) from exc


async def lookup_user_role(
    email: str, *, conn: psycopg.AsyncConnection | None = None
) -> tuple[str, str]:
    """Look up a user's role and id by email.

    Returns (role, user_id). Raises UnknownUserError if no row matches.
    """

    async def _query(c: psycopg.AsyncConnection) -> tuple[str, str]:
        cur = await c.execute('SELECT id, role FROM "user" WHERE email = %s', (email,))
        row = await cur.fetchone()
        if row is None:
            raise UnknownUserError(f"No user found with email {email!r}")
        user_id, role = row
        return str(role), str(user_id)

    if conn is not None:
        return await _query(conn)

    async with get_connection() as c:
        return await _query(c)


async def execute_select(
    sql: str,
    *,
    conn: psycopg.AsyncConnection | None = None,
) -> list[dict[str, Any]]:
    """Run a validated SELECT and return rows as a list of dicts.

    Caller is responsible for having already passed `sql` through
    sql_check.check_shape() — this function trusts its input.
    """

    async def _run(c: psycopg.AsyncConnection) -> list[dict[str, Any]]:
        try:
            cur = await c.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            rows = await cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except psycopg.Error as exc:
            raise QueryExecutionError(str(exc)) from exc

    if conn is not None:
        return await _run(conn)

    async with get_connection() as c:
        return await _run(c)
