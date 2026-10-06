"""Session auth via X-Session-Token header only (no query/body)."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query

from machine_limits import SESSION_HEADER


def token_log_prefix(token: str) -> str:
    t = (token or "").strip()
    if len(t) < 8:
        return "…"
    return t[:8] + "…"


def reject_legacy_session_channels(
    *,
    body_token: Optional[str] = None,
    query_token: Optional[str] = None,
) -> None:
    """Reject deprecated query/body session_token (header-only rollout)."""
    for channel, value in (("query", query_token), ("body", body_token)):
        if value is not None and str(value).strip():
            raise HTTPException(
                status_code=401,
                detail={
                    "sucesso": False,
                    "mensagem": (
                        f"session_token via {channel} não é aceito. "
                        f"Envie o header {SESSION_HEADER}."
                    ),
                },
            )


def require_session_token_value(token: str) -> str:
    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "sucesso": False,
                "mensagem": f"session_token ausente. Envie o header {SESSION_HEADER} ou faça login novamente.",
            },
        )
    return token


def resolve_session_from_header(
    header_token: str,
    *,
    body_token: Optional[str] = None,
    query_token: Optional[str] = None,
) -> str:
    reject_legacy_session_channels(body_token=body_token, query_token=query_token)
    return require_session_token_value((header_token or "").strip())


def resolve_session_token_required(
    x_session_token: Annotated[str, Header(alias=SESSION_HEADER)] = "",
    session_token: Annotated[str, Query()] = "",
) -> str:
    return resolve_session_from_header(
        x_session_token,
        query_token=session_token,
    )


SessionTokenDep = Annotated[str, Depends(resolve_session_token_required)]
SessionTokenHeaderDep = Annotated[str, Header(alias=SESSION_HEADER)]
