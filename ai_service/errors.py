"""
errors.py — maps internal exception types to fixed, human-facing
messages.

Every failure path in the pipeline ends up here exactly once. The
point of centralizing this: no call site gets to improvise its own
wording, and no call site can accidentally interpolate a raw exception
(a table name, a stack trace, a provider error body) into what the
user sees. Add a new failure mode by adding one entry to
_MESSAGES_BY_TYPE below — everything else routes through
user_facing_message().

Messages are grouped by ACTIONABILITY, not by cause: a transient
failure (DB timeout, LLM outage) tells the user retrying might help; a
scope/permission failure tells them it won't, because it's not that
kind of problem. This is the one piece of judgment a user actually
needs from an error message — "should I try again?" — everything else
about the failure is an internal detail they can't act on anyway.
"""

from __future__ import annotations

from ai_service.db import DatabaseConnectionError, QueryExecutionError, UnknownUserError
from ai_service.providers import AllProvidersFailedError, NoProviderConfiguredError
from ai_service.sql_check import DisallowedOperationError, OutOfScopeTableError

TRANSIENT_MESSAGE = (
    "I'm here to help with IT asset questions, but I'm having trouble "
    "reaching the system right now. Please try again in a moment."
)

SCOPE_MESSAGE = (
    "I'm bound to helping with IT asset queries — devices, categories, "
    "and (depending on your access) requests and support tickets — and "
    "that one's outside what I can look up for your account. Let me "
    "know if there's something else in that space I can help with."
)

DESTRUCTIVE_MESSAGE = (
    "I can only look up information, not change or delete it."
)

UNKNOWN_USER_MESSAGE = (
    "I couldn't find an account matching that — please check the email "
    "and try again."
)

UNEXPECTED_MESSAGE = (
    "I ran into an unexpected problem trying to answer that. Try "
    "rephrasing your question, or ask again in a moment — if it keeps "
    "happening, it's worth flagging to your IT admin."
)

EMPTY_QUERY_MESSAGE = (
    "I didn't get a question to work with — ask me something about "
    "devices, categories, or (depending on your access) requests and "
    "support tickets."
)

MALFORMED_REQUEST_MESSAGE = (
    "I couldn't understand that request. Try asking your question in "
    "plain language, like \"How many laptops are available?\""
)

# Exception type -> fixed human-facing message. Order matters: more
# specific subclasses must come before their parent classes, since
# lookup walks the MRO and returns the first match.
_MESSAGES_BY_TYPE: dict[type[Exception], str] = {
    # Transient / retry-worthy
    DatabaseConnectionError: TRANSIENT_MESSAGE,
    QueryExecutionError: TRANSIENT_MESSAGE,
    AllProvidersFailedError: TRANSIENT_MESSAGE,
    NoProviderConfiguredError: TRANSIENT_MESSAGE,
    TimeoutError: TRANSIENT_MESSAGE,
    ConnectionError: TRANSIENT_MESSAGE,
    # Not retry-worthy — the request itself isn't something we do
    OutOfScopeTableError: SCOPE_MESSAGE,
    DisallowedOperationError: DESTRUCTIVE_MESSAGE,
    UnknownUserError: UNKNOWN_USER_MESSAGE,
}


def user_facing_message(exc: Exception) -> str:
    """Return the fixed, human-facing message for this exception.

    Walks the exception's MRO so a subclass not explicitly listed
    still matches its nearest registered ancestor (e.g. any other
    PermissionError subclass falls back to SCOPE_MESSAGE). Falls back
    to UNEXPECTED_MESSAGE for anything not recognized at all — the
    raw exception is never returned to the caller, only logged.
    """
    for exc_type in type(exc).__mro__:
        if exc_type in _MESSAGES_BY_TYPE:
            return _MESSAGES_BY_TYPE[exc_type]
    if isinstance(exc, PermissionError):
        return SCOPE_MESSAGE
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return TRANSIENT_MESSAGE
    return UNEXPECTED_MESSAGE
