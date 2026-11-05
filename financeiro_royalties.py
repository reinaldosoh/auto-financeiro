"""
Rotas /financeiro/* — cobrança de royalties via HTTP (sem navegador).

  POST /financeiro/login                 → {status:"aguardando_2fa", login_id} ou session_token
  POST /financeiro/login/2fa             → {session_token, centrais}
  GET  /financeiro/centrais              → cidades do seletor (X-Session-Token)
  POST /financeiro/royalties/rodar       → job em background (X-Session-Token)
  GET  /financeiro/royalties/status/{id} → progresso + resultado por cidade

Persistência: Supabase do machine-finance-hub (env FINANCE_HUB_SUPABASE_URL /
FINANCE_HUB_SUPABASE_SERVICE_KEY). Sem essas variáveis, só `dry_run` com config no body.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import requests
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from machine_financeiro_http import (
    ErroFinanceiro,
    calcular_cenario,
    coletar_cidade,
    concluir_login_2fa,
    iniciar_login,
    listar_centrais,
    mes_passado_sp,
    obter_demonstrativo,
    periodo_rotulo,
    trocar_central,
    validar_ano_mes,
)
from machine_limits import check_heavy_limit, check_login_limits
from machine_notificacao_http import get_session, get_session_email
from machine_limits import SESSION_HEADER

log = logging.getLogger(__name__)
router = APIRouter(prefix="/financeiro", tags=["financeiro"])

JOB_TTL_SEG = 6 * 60 * 60
PAUSA_ENTRE_CIDADES_SEG = float(os.environ.get("FINANCEIRO_PAUSA_CIDADES_SEG", "1.5") or "1.5")


def _erro(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ErroFinanceiro):
        return HTTPException(exc.status, {"sucesso": False, "codigo": exc.codigo, "mensagem": str(exc)})
    return HTTPException(400, {"sucesso": False, "codigo": "erro", "mensagem": str(exc)})


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _sessao(token: str):
    http = get_session(token or "")
    if not http:
        raise HTTPException(
            401,
            {"sucesso": False, "codigo": "sessao_expirada", "mensagem": "session_token inválido ou expirado."},
        )
    return http


# ───────────────────────── Supabase (finance-hub) ─────────────────────────


class FinanceHub:
    def __init__(self) -> None:
        self.url = (os.environ.get("FINANCE_HUB_SUPABASE_URL") or "").rstrip("/")
        self.key = os.environ.get("FINANCE_HUB_SUPABASE_SERVICE_KEY") or ""
        self.empresa_id = os.environ.get("FINANCE_HUB_EMPRESA_ID") or ""

    @property
    def ativo(self) -> bool:
        return bool(self.url and self.key)

    def _h(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        return {"apikey": self.key, "Authorization": f"Bearer {self.key}", **(extra or {})}

    def _get(self, tabela: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        r = requests.get(f"{self.url}/rest/v1/{tabela}", headers=self._h(), params=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {tabela}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def cidades_cenario_01(self) -> List[Dict[str, Any]]:
        params = {
            "select": "id,nome,empresa_id,bandeira_id,cenario_cobranca,taxa_valor,taxa_tipo,"
            "taxa_seguro_app,taxa_seguro_app_tipo",
            "cenario_cobranca": "ov.{cenario_01,cenario_02}",
            "bandeira_id": "not.is.null",
        }
        if self.empresa_id:
            params["empresa_id"] = f"eq.{self.empresa_id}"
        return self._get("cidades", params)

    def empresa(self, empresa_id: str) -> Dict[str, Any]:
        rows = self._get(
            "empresas",
            {
                "select": "id,email,taxa_seguro_app_global,taxa_seguro_app_global_tipo,"
                "taxa_global_tarifa_boleto,taxa_global_tarifa_boleto_tipo,"
                "taxa_global_corridas_finalizadas,taxa_global_corridas_nao_finalizadas,"
                "taxa_global_rota_exibida_estimativa,taxa_global_rota_exibida_viagem,"
                "taxa_global_validacao_cpf,taxa_global_sms,taxa_global_taxa_saque_pix",
                "id": f"eq.{empresa_id}",
            },
        )
        if not rows:
            raise RuntimeError(f"Empresa {empresa_id} não encontrada no finance-hub.")
        return rows[0]

    def cobranca_existe(self, cidade_id: str, cenario: str, rotulo: str) -> bool:
        rows = self._get(
            "cobrancas",
            {"select": "id", "cidade_id": f"eq.{cidade_id}", "cenario": f"eq.{cenario}", "periodo_rotulo": f"eq.{rotulo}"},
        )
        return bool(rows)

    def inserir_cobranca(self, linha: Dict[str, Any]) -> None:
        r = requests.post(
            f"{self.url}/rest/v1/cobrancas",
            headers=self._h({"Content-Type": "application/json", "Prefer": "return=minimal"}),
            json=linha,
            timeout=30,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase insert cobranca: HTTP {r.status_code} {r.text[:200]}")

    def marcar_rodou(self, cidade_id: str) -> None:
        requests.patch(
            f"{self.url}/rest/v1/cidades",
            headers=self._h({"Content-Type": "application/json", "Prefer": "return=minimal"}),
            params={"id": f"eq.{cidade_id}"},
            json={"rodou_mes_atual": True},
            timeout=30,
        )


_MAPA_TABELA_HUB = {
    "corridas_finalizadas": "taxa_global_corridas_finalizadas",
    "corridas_nao_finalizadas": "taxa_global_corridas_nao_finalizadas",
    "rota_estimativa": "taxa_global_rota_exibida_estimativa",
    "rota_viagem": "taxa_global_rota_exibida_viagem",
    "validacao_cpf": "taxa_global_validacao_cpf",
    "sms": "taxa_global_sms",
    "saque_pix": "taxa_global_taxa_saque_pix",
    "tarifa_boleto": "taxa_global_tarifa_boleto",
}


def _cfg_de_supabase(cidade: Dict[str, Any], empresa: Dict[str, Any]) -> Dict[str, Any]:
    # Preços > 0 cadastrados no finance-hub sobrescrevem a tabela padrão; transbordo fica no padrão.
    tabela = {k: float(empresa[c]) for k, c in _MAPA_TABELA_HUB.items() if empresa.get(c)}
    return {
        "tabela_ubiz": tabela,
        "taxa_valor": cidade.get("taxa_valor"),
        "taxa_tipo": cidade.get("taxa_tipo"),
        "taxa_seguro_app": cidade.get("taxa_seguro_app"),
        "taxa_seguro_app_tipo": cidade.get("taxa_seguro_app_tipo"),
        "taxa_seguro_app_global": empresa.get("taxa_seguro_app_global"),
        "taxa_seguro_app_global_tipo": empresa.get("taxa_seguro_app_global_tipo"),
        "tarifa_boleto": empresa.get("taxa_global_tarifa_boleto"),
        "tarifa_boleto_tipo": empresa.get("taxa_global_tarifa_boleto_tipo"),
    }


# ───────────────────────── modelos ─────────────────────────


class LoginInput(BaseModel):
    email: Optional[str] = None
    senha: Optional[str] = None


class Login2FAInput(BaseModel):
    login_id: str
    codigo: str


class CfgCidade(BaseModel):
    taxa_valor: float = Field(..., description="% (ou valor fixo) de royalty da Ubiz para a cidade")
    taxa_tipo: Literal["percentual", "fixo"] = "percentual"
    taxa_seguro_app: float = 0
    taxa_seguro_app_tipo: Literal["percentual", "fixo"] = "fixo"
    taxa_seguro_app_global: float = 0
    taxa_seguro_app_global_tipo: Literal["percentual", "fixo"] = "fixo"
    tarifa_boleto: float = 3.50
    tarifa_boleto_tipo: Literal["percentual", "fixo"] = "fixo"
    descontar_seguro: Optional[bool] = Field(
        None, description="Padrão: desconta no cenário 01, não desconta no cenário 02"
    )
    tabela_ubiz: Optional[Dict[str, float]] = Field(
        None, description="Preços Ubiz por item do demonstrativo (padrão: TABELA_UBIZ_PADRAO)"
    )


class RodarInput(BaseModel):
    ano_mes: Optional[str] = Field(None, description="AAAA-MM; padrão = mês passado (São Paulo)")
    bandeira_ids: Optional[List[str]] = None
    dry_run: bool = True
    sobrescrever: bool = False
    config_por_bandeira: Optional[Dict[str, CfgCidade]] = Field(
        None, description="Sem Supabase (ou para simular): config por bandeira_id"
    )
    config_padrao: Optional[CfgCidade] = None

    @field_validator("bandeira_ids")
    @classmethod
    def _lim(cls, v):
        if v is not None and len(v) > 200:
            raise ValueError("Máximo de 200 cidades por execução.")
        return [str(x) for x in v] if v is not None else v


# ───────────────────────── jobs ─────────────────────────

_jobs: Dict[str, Dict[str, Any]] = {}
_sessoes_ocupadas: Dict[str, str] = {}
_jobs_lock = threading.Lock()


def _limpar_jobs() -> None:
    corte = time.time() - JOB_TTL_SEG
    for k in [k for k, j in _jobs.items() if j["atualizado_em"] < corte and j["status"] != "processing"]:
        _jobs.pop(k, None)


def _job_publico(job: Dict[str, Any]) -> Dict[str, Any]:
    cidades = job["cidades"]
    ok = [c for c in cidades if c["status"] in ("calculado", "gravado")]
    return {
        "sucesso": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "erro": job["erro"],
        "ano_mes": job["ano_mes"],
        "periodo_rotulo": job["periodo_rotulo"],
        "dry_run": job["dry_run"],
        "progresso": {
            "total": len(cidades),
            "concluidas": sum(1 for c in cidades if c["status"] not in ("aguardando", "processando")),
            "com_erro": sum(1 for c in cidades if c["status"] == "erro"),
        },
        "totais": {
            "royalty": round(sum(c["calculo"]["royalty"] for c in ok), 2),
            "valor_final": round(sum(c["calculo"]["valor_final"] for c in ok), 2),
        },
        "cidades": [{k: v for k, v in c.items() if not k.startswith("_")} for c in cidades],
        "criado_em": int(job["criado_em"]),
        "atualizado_em": int(job["atualizado_em"]),
    }


def _processar_cidade(job: Dict[str, Any], item: Dict[str, Any], http, hub: FinanceHub) -> None:
    item["status"] = "processando"
    dem_erro = item.pop("_dem_erro", None)
    if dem_erro:
        raise dem_erro
    coleta = coletar_cidade(http, item["bandeira_id"], job["ano_mes"], demonstrativo=item.pop("_dem", None))
    item["entradas"] = coleta
    calc = calcular_cenario(coleta["entradas_calculo"], item["cfg"])
    item["calculo"] = calc
    item["avisos"] = coleta["ganhos"].get("avisos", []) + calc["avisos"]
    item["status"] = "calculado"

    if job["dry_run"] or not item.get("cidade_id"):
        if not job["dry_run"]:
            item["avisos"].append("Cidade sem cadastro no finance-hub: não gravado.")
        return
    if not job["sobrescrever"] and hub.cobranca_existe(item["cidade_id"], calc["cenario"], job["periodo_rotulo"]):
        item["avisos"].append(f"Já existe cobrança {calc['cenario']} deste mês: não gravado (use sobrescrever).")
        return

    agora = datetime.now(timezone.utc).isoformat()
    base = {
        "cidade_id": item["cidade_id"],
        "empresa_id": item["empresa_id"],
        "email_conta": job["email"],
        "periodo_rotulo": job["periodo_rotulo"],
        "extraido_em": agora,
        "status_pagamento": "pendente",
    }
    if "seguro_app" in (item.get("cenarios") or []) and calc["seguro_app"] > 0:
        seg = calc["seguro_app_detalhe"]
        corridas = coleta["entradas_calculo"]["corridas_finalizadas"]
        hub.inserir_cobranca({
            **base,
            "cenario": "seguro_app",
            "taxa_central": corridas,
            "valor_pago_motorista": 0,
            "valor_bruto": corridas,
            "taxa_valor": seg["taxa"],
            "taxa_tipo": seg["tipo"],
            "valor_taxa_aplicada": calc["seguro_app"],
            "valor_final": calc["seguro_app"],
            "payload": {"origem": "vps_financeiro", "bandeira_id": item["bandeira_id"]},
        })
    hub.inserir_cobranca({
        **base,
        "cenario": calc["cenario"],
        "taxa_central": calc["saldo_01"],
        "valor_pago_motorista": round(calc["ganhos_gerais"] - calc["saldo_01"], 2),
        "valor_bruto": calc["ganhos_gerais"],
        "taxa_valor": calc["taxa_cidade"],
        "taxa_tipo": calc["taxa_cidade_tipo"],
        "valor_taxa_aplicada": calc["royalty"],
        "valor_final": calc["valor_final"],
        "payload": {"origem": "vps_financeiro", "bandeira_id": item["bandeira_id"], "entradas": coleta, "calculo": calc},
    })
    hub.marcar_rodou(item["cidade_id"])
    item["status"] = "gravado"


def _executar_job(job: Dict[str, Any], http, hub: FinanceHub) -> None:
    """Sequencial: trocar central muda estado da sessão PHP na Machine."""
    central_original = None
    try:
        try:
            central_original = next((c["bandeira_id"] for c in listar_centrais(http) if c["atual"]), None)
        except Exception as exc:
            log.warning("job financeiro %s: central original desconhecida: %s", job["job_id"], exc)
        # 1ª passada: demonstrativos (boleto Machine) enquanto a sessão está na matriz.
        for item in job["cidades"]:
            try:
                item["_dem"] = obter_demonstrativo(http, item["bandeira_id"], job["ano_mes"])
            except ErroFinanceiro as exc:
                if exc.codigo == "sessao_expirada":
                    raise
                item["_dem_erro"] = exc
            except Exception as exc:
                item["_dem_erro"] = ErroFinanceiro("demonstrativo_falhou", str(exc))
            job["atualizado_em"] = time.time()
        # 2ª passada: por cidade, troca de central → ganhos → taxas → cálculo.
        for item in job["cidades"]:
            try:
                _processar_cidade(job, item, http, hub)
            except ErroFinanceiro as exc:
                item.update(status="erro", erro={"codigo": exc.codigo, "mensagem": str(exc),
                                                 "diagnostico": getattr(exc, "diagnostico", None)})
                if exc.codigo == "sessao_expirada":
                    raise
            except Exception as exc:
                log.exception("job financeiro %s cidade %s", job["job_id"], item["bandeira_id"])
                item.update(status="erro", erro={"codigo": "erro", "mensagem": str(exc)})
            job["atualizado_em"] = time.time()
            get_session(job["session_token"])  # renova TTL da sessão durante a rodada
            time.sleep(PAUSA_ENTRE_CIDADES_SEG)
        job["status"] = "ready"
    except Exception as exc:
        job["status"] = "erro"
        job["erro"] = str(exc)
        for item in job["cidades"]:
            item.pop("_dem", None)
            item.pop("_dem_erro", None)
            if item["status"] in ("aguardando", "processando"):
                item.update(status="erro", erro={"codigo": "interrompido", "mensagem": str(exc)})
    finally:
        if central_original and job["status"] != "erro":
            try:
                trocar_central(http, central_original)
            except Exception as exc:
                log.warning("job financeiro %s: falha ao voltar para central %s: %s", job["job_id"], central_original, exc)
        with _jobs_lock:
            _sessoes_ocupadas.pop(job["session_token"], None)
        job["atualizado_em"] = time.time()


# ───────────────────────── rotas ─────────────────────────


@router.post("/login")
async def financeiro_login(inp: LoginInput, request: Request):
    """Login da matriz. Se a Machine pedir 2FA, devolve `login_id` para a tela de código."""
    email = (inp.email or os.environ.get("FINANCEIRO_MATRIZ_EMAIL") or "").strip()
    senha = inp.senha or os.environ.get("FINANCEIRO_MATRIZ_SENHA") or ""
    if not email or not senha:
        raise HTTPException(400, {"sucesso": False, "codigo": "credenciais_ausentes", "mensagem": "Informe email e senha da matriz."})
    check_login_limits(email, _client_ip(request))
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: iniciar_login(email, senha))
    except Exception as exc:
        raise _erro(exc)


@router.post("/login/2fa")
async def financeiro_login_2fa(inp: Login2FAInput):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: concluir_login_2fa(inp.login_id, inp.codigo))
    except Exception as exc:
        raise _erro(exc)


@router.get("/centrais")
async def financeiro_centrais(x_session_token: str = Header("", alias=SESSION_HEADER)):
    http = _sessao(x_session_token)
    loop = asyncio.get_event_loop()
    try:
        return {"sucesso": True, "centrais": await loop.run_in_executor(None, lambda: listar_centrais(http))}
    except Exception as exc:
        raise _erro(exc)


@router.post("/royalties/rodar")
async def financeiro_royalties_rodar(inp: RodarInput, x_session_token: str = Header("", alias=SESSION_HEADER)):
    """
    Busca ganhos, taxas e demonstrativo de cada cidade e calcula o cenário 01.
    Responde na hora com `job_id`; acompanhe em GET /financeiro/royalties/status/{job_id}.
    """
    http = _sessao(x_session_token)
    try:
        ano_mes = inp.ano_mes or mes_passado_sp()
        validar_ano_mes(ano_mes)
        check_heavy_limit("financeiro")
    except Exception as exc:
        raise _erro(exc)

    hub = FinanceHub()
    if not inp.dry_run and not hub.ativo:
        raise HTTPException(400, {"sucesso": False, "codigo": "supabase_nao_configurado",
                                  "mensagem": "dry_run=false exige FINANCE_HUB_SUPABASE_URL/SERVICE_KEY na VPS."})

    loop = asyncio.get_event_loop()
    try:
        cidades_db: List[Dict[str, Any]] = await loop.run_in_executor(None, hub.cidades_cenario_01) if hub.ativo else []
        empresas: Dict[str, Dict[str, Any]] = {}
        for c in cidades_db:
            if c["empresa_id"] not in empresas:
                empresas[c["empresa_id"]] = await loop.run_in_executor(None, lambda eid=c["empresa_id"]: hub.empresa(eid))
    except Exception as exc:
        raise _erro(exc)

    por_bandeira = {str(c["bandeira_id"]): c for c in cidades_db}
    alvos = inp.bandeira_ids or list(por_bandeira.keys())
    if not alvos:
        raise HTTPException(400, {"sucesso": False, "codigo": "sem_cidades",
                                  "mensagem": "Nenhuma cidade: envie bandeira_ids ou cadastre bandeira_id + cenario_01/02 no finance-hub."})

    itens = []
    for bid in alvos:
        cad = por_bandeira.get(bid)
        override = (inp.config_por_bandeira or {}).get(bid) or inp.config_padrao
        if override:
            cfg = override.model_dump()
        elif cad:
            cfg = _cfg_de_supabase(cad, empresas[cad["empresa_id"]])
        else:
            raise HTTPException(400, {"sucesso": False, "codigo": "cidade_sem_config",
                                      "mensagem": f"Bandeira {bid} sem cadastro no finance-hub e sem config_por_bandeira."})
        itens.append({
            "bandeira_id": bid,
            "nome": cad.get("nome") if cad else None,
            "cidade_id": cad.get("id") if cad else None,
            "empresa_id": cad.get("empresa_id") if cad else None,
            "cenarios": cad.get("cenario_cobranca") if cad else [],
            "cfg": cfg,
            "status": "aguardando",
            "entradas": None,
            "calculo": None,
            "avisos": [],
            "erro": None,
        })

    with _jobs_lock:
        _limpar_jobs()
        if x_session_token in _sessoes_ocupadas:
            raise HTTPException(409, {"sucesso": False, "codigo": "rodada_em_andamento",
                                      "mensagem": "Já existe uma rodada com esta sessão.",
                                      "job_id": _sessoes_ocupadas[x_session_token]})
        agora = time.time()
        job = {
            "job_id": str(uuid.uuid4()),
            "session_token": x_session_token,
            "email": get_session_email(x_session_token) or "",
            "ano_mes": ano_mes,
            "periodo_rotulo": periodo_rotulo(ano_mes),
            "dry_run": inp.dry_run,
            "sobrescrever": inp.sobrescrever,
            "status": "processing",
            "erro": None,
            "criado_em": agora,
            "atualizado_em": agora,
            "cidades": itens,
        }
        _jobs[job["job_id"]] = job
        _sessoes_ocupadas[x_session_token] = job["job_id"]

    threading.Thread(target=_executar_job, args=(job, http, hub), daemon=True).start()
    return _job_publico(job)


@router.get("/royalties/status/{job_id}")
async def financeiro_royalties_status(job_id: str, x_session_token: str = Header("", alias=SESSION_HEADER)):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, {"sucesso": False, "codigo": "job_nao_encontrado", "mensagem": "job_id não encontrado ou expirado."})
    if not secrets.compare_digest(job["session_token"], x_session_token or ""):
        raise HTTPException(401, {"sucesso": False, "codigo": "sessao_invalida", "mensagem": "session_token não corresponde a este job."})
    return _job_publico(job)


def registrar_rotas(app) -> None:
    """Registra as rotas direto no app (APIRoute), herdando a dependência global de X-API-Key."""
    for rota in router.routes:
        app.add_api_route(rota.path, rota.endpoint, methods=list(rota.methods), tags=["financeiro"])
