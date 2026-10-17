#!/usr/bin/env python3
"""Smoke: ligar/desligar exibir no app (Maçaneta). Credenciais: SMOKE_CIDADE_* ou Supabase Mariana."""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from auto_2fa import gerar_codigo
from machine_categoria_http import (
    definir_exibicao_app_por_nome,
    listar_categorias_tarifa,
)
from machine_notificacao_http import get_session, login_painel


def _creds():
    email = os.environ.get("SMOKE_CIDADE_EMAIL", "").strip()
    senha = os.environ.get("SMOKE_CIDADE_SENHA", "").strip()
    totp = os.environ.get("SMOKE_CIDADE_TOTP", "").replace(" ", "")
    if email and senha:
        return email, senha, totp
    sys.path.insert(0, os.path.join(_REPO, "..", "radar-sync", "notification_ubiz", "scripts"))
    from credenciais_supabase_deploy import obter_supabase_access_token
    import json
    import urllib.request

    token = obter_supabase_access_token()
    cid = os.environ.get(
        "SMOKE_CIDADE_ID", "4dcff3b3-6867-493a-8d3e-26a99c8702d4"
    )
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/lxapogvzccnusjmbthnj/database/query",
        method="POST",
        data=json.dumps(
            {
                "query": f"SELECT usuario, senha, automation_totp FROM cidades WHERE id = '{cid}'::uuid"
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    row = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())[0]
    return (
        row["usuario"].strip(),
        row["senha"].strip(),
        (row.get("automation_totp") or "").replace(" ", ""),
    )


def main() -> int:
    nome = os.environ.get("SMOKE_CATEGORIA_NOME", "Maçaneta")
    email, senha, totp = _creds()
    login = login_painel(
        email=email,
        senha=senha,
        chave_secreta=totp,
        gerar_codigo_fn=gerar_codigo,
    )
    http = get_session(login["session_token"])
    kw = {"chave_secreta": totp, "gerar_codigo_fn": gerar_codigo}

    print("→ exibir ON")
    definir_exibicao_app_por_nome(http, nome, True, **kw)
    cat = next(c for c in listar_categorias_tarifa(http) if c["nome"] == nome)
    assert cat["exibir_app"] is True, cat

    print("→ exibir OFF")
    definir_exibicao_app_por_nome(http, nome, False, **kw)
    cat = next(c for c in listar_categorias_tarifa(http) if c["nome"] == nome)
    assert cat["exibir_app"] is False, cat

    print("OK — exibir ligou e desligou; lista tarifa bate com o formulário.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
