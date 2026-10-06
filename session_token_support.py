"""Resolve session_token from header (preferred) or deprecated query/body."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query

from machine_limits import SESSION_HEADER


def token_log_prefix(token: str) -> str:
    t = (token or "").strip()
    if len(t) < 8:
        return "…"
    return t[:8] + "…"


def merge_session_token(
    body_token: Optional[str],
    header_token: str = "",
    query_token: str = "",
) -> str:
    for candidate in (header_token, body_token, query_token):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def require_session_token_value(token: str) -> str:
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "sucesso": False,
                "mensagem": "session_token ausente. Envie o header X-Session-Token ou faça login novamente.",
            },
        )
    return token


def resolve_session_token_required(
    x_session_token: Annotated[str, Header(alias=SESSION_HEADER)] = "",
    session_token: Annotated[str, Query()] = "",
) -> str:
    return require_session_token_value(merge_session_token(None, x_session_token, session_token))


SessionTokenDep = Annotated[str, Depends(resolve_session_token_required)]
SessionTokenHeaderDep = Annotated[str, Header(alias=SESSION_HEADER)]
