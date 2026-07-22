"""
API FastAPI para disparar a automação de 2FA e anúncios no TaxiMachine.

Primeiro acesso (conta com 2FA, usuário só tem email e senha):
    1) Chame POST {BASE_URL}/autenticar com {"email", "senha"} — sem chave_secreta.
       A automação conclui o assistente de 2FA no TaxiMachine, obtém o segredo TOTP e
       grava em chaves_totp.json no disco do servidor (o corpo de resposta também traz chave_totp).
    2) Nas rotas seguintes (anúncio, remover, etc.), envie só email e senha; omita chave_secreta.
       O servidor usa o segredo já salvo. Opcional: continue enviando chave_secreta se quiser
       sobrescrever/forçar um segredo conhecido.
    Em Docker/VPS, use volume persistente apontando para o diretório que contém chaves_totp.json,
    senão a chave se perde a cada redeploy e o passo (1) precisa ser refeito.

Uso local:
    uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload

Chamada externa (substitua BASE_URL pela URL pública do serviço, ex. Easypanel):
    POST {BASE_URL}/anuncio-passageiro
    Content-Type: application/json
    Corpo JSON (campos principais):
      email, senha, chave_secreta (opcional após /autenticar ter gravado a chave no servidor),
      imagem_url OU imagem_base64,
      link_anuncio (obrigatório para passageiro),
      selecionar_todas (default true), headless (default true), manter_aberto (default false)
    Timeout recomendado no cliente: 300–600 s.

Endpoints:
    POST /autenticar       - Setup 2FA inicial (ou login se já configurado)
    POST /login            - Login com 2FA (chave salva no servidor)
    GET  /chaves           - Lista emails com chave TOTP salva no disco do servidor
    POST /codigo           - Gera código TOTP para um email
    POST /anuncio-motorista
    POST /remover-anuncio
    POST /anuncio-passageiro
    POST /remover-anuncio-passageiro  (JSON opcional: indice inteiro 0-based; omitir = remover todos)
    POST /banner-corrida              - Campanha no ciclo da corrida (app passageiro)
    POST /remover-banner-corrida      - Remove/desativa campanha no ciclo da corrida
    POST /notificacao/login           - Login HTTP no painel (cookie de sessão, sem Selenium)
    GET  /notificacao/bandeiras       - Centrais disponíveis na conta
    GET  /notificacao/categorias      - Categorias para filtros de notificação em massa
    POST /notificacao/categorias      - Mesmo que GET; aceita credenciais ou session_token
    POST /notificacao/filtrar         - Gera relatório de destinatários (Avançar)
    GET  /notificacao/status/{id}     - Polling do relatório assíncrono
    GET  /notificacao/total/{id}      - Total de destinatários do relatório
    POST /notificacao/aguardar        - Polling até relatório ficar pronto
    POST /notificacao/enviar          - Enviar notificação agora
    POST /notificacao/agendar         - Agendar notificação
    POST /notificacao/autenticar-acao - 2FA para ação sensível (envio)
    GET  /notificacao/listar          - Lista campanhas do painel
    DELETE /notificacao/{id}          - Cancela/exclui campanha agendada
    POST /financeiro_completo_02     - Fluxo financeiro (ganhos mês passado + taxas), JSON local;
                                       default: sem webhook, navegador visível, mantém aberto em background.
    POST /financeiro_historico_corridas - Histórico corridas (filtro mês anterior) + taxa central/seguro;
                                          webhook de teste default; ver corpo FinanceiroHistoricoCorridasInput.
"""

import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from auto_2fa import executar_automacao, executar_login, executar_login_recursos_premium, executar_adicionar_anuncio_motorista, executar_remover_anuncio_motorista, executar_adicionar_anuncio_passageiro, executar_remover_anuncio_passageiro, executar_adicionar_campanha_corrida, executar_remover_campanha_corrida, carregar_chaves, obter_chave, gerar_codigo
from machine_notificacao_http import (
    aguardar_relatorio,
    autenticar_acao_2fa,
    cancelar_notificacao,
    enviar_ou_agendar,
    filtrar_destinatarios,
    form_from_dict,
    get_session,
    get_session_email,
    listar_notificacoes,
    login_painel,
    obter_bandeiras,
    obter_categorias,
    obter_categorias_com_credenciais,
    obter_total_destinatarios,
    verificar_status_relatorio,
)
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import base64
import uuid
import tempfile
import os
import urllib.request
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI(
    title="TaxiMachine 2FA Automação",
    description="API para automatizar o processo de configuração 2FA no TaxiMachine",
    version="1.0.0",
)

executor = ThreadPoolExecutor(max_workers=3)


class CredenciaisInput(BaseModel):
    email: str
    senha: str
    headless: bool = False
    manter_aberto: bool = True

class RemoverAnuncioInput(BaseModel):
    email: str
    senha: str
    # Opcional após POST /autenticar: o servidor lê chaves_totp.json se omitido (ver docstring do módulo).
    chave_secreta: str = None
    headless: bool = True
    manter_aberto: bool = False
    # /remover-anuncio-passageiro: use o dom_slot_idx do disparo (sufixo DOM) OU posição 0-based na lista.
    # Omita indice para remover todos.
    indice: Optional[int] = None

    @field_validator("indice", mode="before")
    @classmethod
    def _coerce_indice(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("null", "none"):
                return None
            return int(s)
        return v

    @field_validator("headless", "manter_aberto", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "sim", "on")
        return v


class AnuncioMotoristaInput(BaseModel):
    email: str
    senha: str
    chave_secreta: str = None
    headless: bool = True
    manter_aberto: bool = False
    imagem_url: str = None
    imagem_base64: str = None
    link_anuncio: str = ""
    selecionar_todas: bool = True


class BannerCorridaInput(BaseModel):
    email: str
    senha: str
    chave_secreta: str = None
    headless: bool = True
    manter_aberto: bool = False
    imagem_url: str = None
    imagem_base64: str = None
    link_campanha: str = ""
    selecionar_todas: bool = True
    limite_corridas: int = 1000
    data_inicio: Optional[str] = None
    data_fim: Optional[str] = None

    @field_validator("headless", "manter_aberto", "selecionar_todas", mode="before")
    @classmethod
    def _coerce_bool_banner(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "sim", "on")
        return v


class FinanceiroCompleto02Input(BaseModel):
    """Dispara fluxo_financeiro_completo: extrai dados e grava dados_financeiro_completo.json."""

    email: str
    senha: str
    headless: bool = False
    manter_aberto: bool = True
    enviar_webhook: bool = False

    @field_validator("headless", "manter_aberto", "enviar_webhook", mode="before")
    @classmethod
    def _coerce_bool_fc02(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "sim", "on")
        return v


class FinanceiroHistoricoCorridasInput(BaseModel):
    """Histórico de corridas (mês anterior) + taxas; grava dados_historico_corridas_taxas.json."""

    email: str
    senha: str
    headless: bool = True
    manter_aberto: bool = False
    enviar_webhook: bool = True
    webhook_url: Optional[str] = None

    @field_validator("headless", "manter_aberto", "enviar_webhook", mode="before")
    @classmethod
    def _coerce_bool_hist(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "sim", "on")
        return v


class ResultadoOutput(BaseModel):
    sucesso: bool
    email: str
    chave_totp: str
    mensagem: str
    verificacao: Optional[Dict[str, Any]] = None


class NotificacaoLoginInput(BaseModel):
    email: str
    senha: str
    codigo_2fa: Optional[str] = None
    chave_secreta: Optional[str] = None


class NotificacaoCategoriasInput(BaseModel):
    session_token: Optional[str] = None
    email: Optional[str] = None
    senha: Optional[str] = None
    bandeira_id: Optional[str] = None
    codigo_2fa: Optional[str] = None
    chave_secreta: Optional[str] = None


class NotificacaoSessionInput(BaseModel):
    session_token: str


class NotificacaoCampanhaInput(BaseModel):
    session_token: str
    titulo: Optional[str] = ""
    mensagem: str
    destinatario: str = "D"
    bandeira_id: Optional[str] = None
    periodo: str = "hoje"
    quantidade_corridas_operacao: str = "N"
    quantidade_corridas: Optional[str] = "0"
    periodo_cadastro: str = "selecione"
    data_inicial: Optional[str] = ""
    data_final: Optional[str] = ""
    data_inicial_cadastro: Optional[str] = ""
    data_final_cadastro: Optional[str] = ""
    categorias: Optional[List[str]] = None
    generos: Optional[List[str]] = None
    status_taxi: Optional[str] = ""
    sistema_operacional_android: Optional[str] = "0"
    sistema_operacional_ios: Optional[str] = "0"
    cliente_id: Optional[str] = ""
    codigo_2fa_acao: Optional[str] = None
    chave_secreta: Optional[str] = None


class NotificacaoAgendarInput(NotificacaoCampanhaInput):
    data_envio: str
    hora_envio: str


class NotificacaoAguardarInput(BaseModel):
    session_token: str
    report_id: int
    timeout_seg: int = 120


class NotificacaoAutenticarAcaoInput(BaseModel):
    session_token: str
    codigo_2fa: Optional[str] = None
    chave_secreta: Optional[str] = None


class NotificacaoCancelarInput(BaseModel):
    session_token: str
    destinatario: str = "D"


@app.post("/autenticar", response_model=ResultadoOutput)
async def autenticar(creds: CredenciaisInput):
    """
    Recebe email e senha, executa a automação de login + 2FA no TaxiMachine.
    Retorna a chave TOTP e o status da operação.
    """
    log.info("Requisição recebida para: %s", creds.email)

    from functools import partial
    loop = asyncio.get_event_loop()
    resultado = await loop.run_in_executor(
        executor,
        partial(executar_automacao, creds.email, creds.senha, creds.headless, creds.manter_aberto),
    )

    if not resultado["sucesso"]:
        log.warning("Falha para %s: %s", creds.email, resultado["mensagem"])

    return ResultadoOutput(**resultado)


@app.post("/autenticar/lote", response_model=list[ResultadoOutput])
async def autenticar_lote(lista: list[CredenciaisInput]):
    """
    Recebe uma lista de credenciais e processa todas sequencialmente.
    """
    log.info("Requisição em lote recebida: %d contas", len(lista))
    resultados = []

    for creds in lista:
        log.info("Processando: %s", creds.email)
        loop = asyncio.get_event_loop()
        resultado = await loop.run_in_executor(
            executor,
            executar_automacao,
            creds.email,
            creds.senha,
            creds.headless,
        )
        resultados.append(ResultadoOutput(**resultado))

    return resultados


@app.post("/login", response_model=ResultadoOutput)
async def login(creds: CredenciaisInput):
    """
    Login em conta com 2FA já configurado.
    Usa a chave TOTP salva para gerar o código automaticamente.
    """
    log.info("Login requisitado para: %s", creds.email)

    from functools import partial
    loop = asyncio.get_event_loop()
    resultado = await loop.run_in_executor(
        executor,
        partial(executar_login, creds.email, creds.senha, creds.headless, creds.manter_aberto),
    )

    if not resultado["sucesso"]:
        log.warning("Falha login para %s: %s", creds.email, resultado["mensagem"])

    return ResultadoOutput(**resultado)


@app.get("/chaves")
async def listar_chaves():
    """Lista todos os emails que têm chave TOTP salva."""
    dados = carregar_chaves()
    return {
        "total": len(dados),
        "contas": list(dados.keys()),
    }


class CodigoInput(BaseModel):
    email: str


@app.post("/codigo")
async def gerar_codigo_endpoint(inp: CodigoInput):
    """
    Gera o código TOTP de 6 dígitos para um email com chave salva.
    Útil para obter o código sem abrir o browser.
    """
    chave = obter_chave(inp.email)
    if not chave:
        return {"sucesso": False, "codigo": "", "mensagem": "Chave TOTP não encontrada para este email."}
    codigo = gerar_codigo(chave)
    return {"sucesso": True, "codigo": codigo, "email": inp.email}


@app.post("/recursos-premium", response_model=ResultadoOutput)
async def recursos_premium(creds: CredenciaisInput):
    """
    Recebe email e senha, executa a automação de login (com fallback para login_2fa/setup)
    e navega para a página de Recursos Premium (Configurações > Gerais).
    """
    log.info("Requisição (recursos_premium) recebida para: %s", creds.email)

    from functools import partial
    loop = asyncio.get_event_loop()
    
    # Criar wrapper com os parâmetros
    func = partial(
        executar_login_recursos_premium,
        email=creds.email,
        senha=creds.senha,
        headless=creds.headless,
        manter_aberto=creds.manter_aberto
    )

    try:
        resultado = await loop.run_in_executor(executor, func)
        if not resultado.get("sucesso", False):
             raise HTTPException(status_code=400, detail=resultado)
        if "chave_totp" not in resultado or resultado["chave_totp"] is None:
             resultado["chave_totp"] = ""
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        log.error("Erro na thread /recursos-premium: %s", e)
        raise HTTPException(
            status_code=500, detail={"sucesso": False, "mensagem": f"Erro interno: {e}"}
        )

@app.post("/anuncio-motorista", response_model=ResultadoOutput)
async def anuncio_motorista(input_data: AnuncioMotoristaInput):
    """
    Recebe credenciais e infomações do anúncio (imagem, link, checkbox).
    A imagem pode ser passada via URL pública (imagem_url) ou em base64 (imagem_base64).
    Faz login, navega para Recursos Premium e preenche o anúncio na seção de motoristas.
    """
    log.info("Requisição (anuncio-motorista) recebida para: %s", input_data.email)

    if not input_data.imagem_url and not input_data.imagem_base64:
        raise HTTPException(status_code=400, detail={"sucesso": False, "mensagem": "Você deve informar 'imagem_url' ou 'imagem_base64'."})

    tmp_imagem_path = os.path.join(tempfile.gettempdir(), f"anuncio_{uuid.uuid4().hex}.png")

    if input_data.imagem_base64:
        try:
            # Em caso de enviar prefixo data:image/png;base64,
            base64_data = input_data.imagem_base64
            if "," in base64_data:
                base64_data = base64_data.split(",")[1]
            with open(tmp_imagem_path, "wb") as f:
                f.write(base64.b64decode(base64_data))
        except Exception as e:
            raise HTTPException(status_code=400, detail={"sucesso": False, "mensagem": f"Erro ao decodificar imagem base64: {e}"})
    elif input_data.imagem_url:
        try:
            import requests as req_lib
            r = req_lib.get(input_data.imagem_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False)
            r.raise_for_status()
            with open(tmp_imagem_path, "wb") as out_file:
                out_file.write(r.content)
        except Exception as e:
            if os.path.exists(tmp_imagem_path):
                os.remove(tmp_imagem_path)
            raise HTTPException(status_code=400, detail={"sucesso": False, "mensagem": f"Erro ao baixar imagem da URL: {e}"})

    from functools import partial
    loop = asyncio.get_event_loop()
    
    # Criar wrapper com os parâmetros
    func = partial(
        executar_adicionar_anuncio_motorista,
        email=input_data.email,
        senha=input_data.senha,
        chave_secreta=input_data.chave_secreta,
        headless=input_data.headless,
        manter_aberto=input_data.manter_aberto,
        imagem_path=tmp_imagem_path,
        link_anuncio=input_data.link_anuncio,
        selecionar_todas=input_data.selecionar_todas
    )

    try:
        resultado = await loop.run_in_executor(executor, func)
        if not resultado.get("sucesso", False):
             raise HTTPException(status_code=400, detail=resultado)
        if "chave_totp" not in resultado or resultado["chave_totp"] is None:
             resultado["chave_totp"] = ""
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        log.error("Erro na thread /anuncio-motorista: %s", e)
        raise HTTPException(
            status_code=500, detail={"sucesso": False, "mensagem": f"Erro interno: {e}"}
        )
    finally:
        if os.path.exists(tmp_imagem_path):
            os.remove(tmp_imagem_path)


@app.post("/remover-anuncio", response_model=ResultadoOutput)
async def remover_anuncio(creds: RemoverAnuncioInput):
    """
    Faz login, navega para Recursos Premium e remove o anúncio ativo
    na seção 'Adicionar anúncio na tela inicial do app motorista'.
    """
    log.info("Requisição (remover-anuncio) recebida para: %s", creds.email)

    from functools import partial
    loop = asyncio.get_event_loop()
    func = partial(
        executar_remover_anuncio_motorista,
        email=creds.email,
        senha=creds.senha,
        chave_secreta=creds.chave_secreta,
        headless=creds.headless,
        manter_aberto=creds.manter_aberto,
    )

    try:
        resultado = await loop.run_in_executor(executor, func)
        if "chave_totp" not in resultado or resultado["chave_totp"] is None:
            resultado["chave_totp"] = ""
        if not resultado.get("sucesso", False):
            raise HTTPException(status_code=400, detail=resultado)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        log.error("Erro na thread /remover-anuncio: %s", e)
        raise HTTPException(
            status_code=500, detail={"sucesso": False, "mensagem": f"Erro interno: {e}"}
        )


@app.post("/anuncio-passageiro", response_model=ResultadoOutput)
async def anuncio_passageiro(input_data: AnuncioMotoristaInput):
    """
    Recebe credenciais e infomações do anúncio (imagem, link, checkbox).
    Faz login, navega para Recursos Premium e preenche o anúncio na seção de passageiros.
    """
    log.info("Requisição (anuncio-passageiro) recebida para: %s", input_data.email)

    if not input_data.imagem_url and not input_data.imagem_base64:
        raise HTTPException(status_code=400, detail={"sucesso": False, "mensagem": "Você deve informar 'imagem_url' ou 'imagem_base64'."})

    if not (input_data.link_anuncio or "").strip():
        raise HTTPException(
            status_code=400,
            detail={"sucesso": False, "mensagem": "O campo 'link_anuncio' é obrigatório para anúncio de passageiro (validação ao salvar no painel)."},
        )

    tmp_imagem_path = os.path.join(tempfile.gettempdir(), f"anuncio_pass_{uuid.uuid4().hex}.png")

    if input_data.imagem_base64:
        try:
            base64_data = input_data.imagem_base64
            if "," in base64_data:
                base64_data = base64_data.split(",")[1]
            with open(tmp_imagem_path, "wb") as f:
                f.write(base64.b64decode(base64_data))
        except Exception as e:
            raise HTTPException(status_code=400, detail={"sucesso": False, "mensagem": f"Erro ao decodificar imagem base64: {e}"})
    elif input_data.imagem_url:
        try:
            import requests as req_lib
            r = req_lib.get(input_data.imagem_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False)
            r.raise_for_status()
            with open(tmp_imagem_path, "wb") as out_file:
                out_file.write(r.content)
        except Exception as e:
            if os.path.exists(tmp_imagem_path):
                os.remove(tmp_imagem_path)
            raise HTTPException(status_code=400, detail={"sucesso": False, "mensagem": f"Erro ao baixar imagem da URL: {e}"})

    from functools import partial
    loop = asyncio.get_event_loop()
    
    func = partial(
        executar_adicionar_anuncio_passageiro,
        email=input_data.email,
        senha=input_data.senha,
        chave_secreta=input_data.chave_secreta,
        headless=input_data.headless,
        manter_aberto=input_data.manter_aberto,
        imagem_path=tmp_imagem_path,
        link_anuncio=input_data.link_anuncio,
        selecionar_todas=input_data.selecionar_todas
    )

    try:
        resultado = await loop.run_in_executor(executor, func)
        if not resultado.get("sucesso", False):
             raise HTTPException(status_code=400, detail=resultado)
        if "chave_totp" not in resultado or resultado["chave_totp"] is None:
             resultado["chave_totp"] = ""
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        log.error("Erro na thread /anuncio-passageiro: %s", e)
        raise HTTPException(
            status_code=500, detail={"sucesso": False, "mensagem": f"Erro interno: {e}"}
        )
    finally:
        if os.path.exists(tmp_imagem_path):
            os.remove(tmp_imagem_path)


@app.post("/banner-corrida", response_model=ResultadoOutput)
async def banner_corrida(input_data: BannerCorridaInput):
    """
    Campanha no ciclo da corrida no app passageiro (Recursos Premium).
    Preenche imagem, link opcional, centrais, limite de corridas e período, e clica em Gravar.
    """
    log.info("Requisição (banner-corrida) recebida para: %s", input_data.email)

    if not input_data.imagem_url and not input_data.imagem_base64:
        raise HTTPException(
            status_code=400,
            detail={"sucesso": False, "mensagem": "Você deve informar 'imagem_url' ou 'imagem_base64'."},
        )

    if input_data.limite_corridas <= 0:
        raise HTTPException(
            status_code=400,
            detail={"sucesso": False, "mensagem": "limite_corridas deve ser maior que zero."},
        )

    tmp_imagem_path = os.path.join(tempfile.gettempdir(), f"banner_corrida_{uuid.uuid4().hex}.png")

    if input_data.imagem_base64:
        try:
            base64_data = input_data.imagem_base64
            if "," in base64_data:
                base64_data = base64_data.split(",")[1]
            with open(tmp_imagem_path, "wb") as f:
                f.write(base64.b64decode(base64_data))
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail={"sucesso": False, "mensagem": f"Erro ao decodificar imagem base64: {e}"},
            )
    elif input_data.imagem_url:
        try:
            import requests as req_lib

            r = req_lib.get(
                input_data.imagem_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
                verify=False,
            )
            r.raise_for_status()
            with open(tmp_imagem_path, "wb") as out_file:
                out_file.write(r.content)
        except Exception as e:
            if os.path.exists(tmp_imagem_path):
                os.remove(tmp_imagem_path)
            raise HTTPException(
                status_code=400,
                detail={"sucesso": False, "mensagem": f"Erro ao baixar imagem da URL: {e}"},
            )

    from functools import partial

    loop = asyncio.get_event_loop()
    func = partial(
        executar_adicionar_campanha_corrida,
        email=input_data.email,
        senha=input_data.senha,
        chave_secreta=input_data.chave_secreta,
        headless=input_data.headless,
        manter_aberto=input_data.manter_aberto,
        imagem_path=tmp_imagem_path,
        link_campanha=input_data.link_campanha,
        selecionar_todas=input_data.selecionar_todas,
        limite_corridas=input_data.limite_corridas,
        data_inicio=input_data.data_inicio,
        data_fim=input_data.data_fim,
    )

    try:
        resultado = await loop.run_in_executor(executor, func)
        if not resultado.get("sucesso", False):
            raise HTTPException(status_code=400, detail=resultado)
        if "chave_totp" not in resultado or resultado["chave_totp"] is None:
            resultado["chave_totp"] = ""
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        log.error("Erro na thread /banner-corrida: %s", e)
        raise HTTPException(
            status_code=500, detail={"sucesso": False, "mensagem": f"Erro interno: {e}"}
        )
    finally:
        if os.path.exists(tmp_imagem_path):
            os.remove(tmp_imagem_path)


@app.post("/remover-banner-corrida", response_model=ResultadoOutput)
async def remover_banner_corrida_endpoint(creds: RemoverAnuncioInput):
    """
    Remove campanha no ciclo da corrida no app passageiro.
    Omita `indice` para desativar o recurso (radio Não) e apagar todas as campanhas.
    Com `indice`, remove só a campanha na posição informada (0-based).
    """
    log.info(
        "Requisição (remover-banner-corrida) para: %s indice=%s",
        creds.email,
        creds.indice,
    )

    from functools import partial

    loop = asyncio.get_event_loop()
    func = partial(
        executar_remover_campanha_corrida,
        email=creds.email,
        senha=creds.senha,
        chave_secreta=creds.chave_secreta,
        headless=creds.headless,
        manter_aberto=creds.manter_aberto,
        indice=creds.indice,
    )

    try:
        resultado = await loop.run_in_executor(executor, func)
        if "chave_totp" not in resultado or resultado["chave_totp"] is None:
            resultado["chave_totp"] = ""
        if not resultado.get("sucesso", False):
            raise HTTPException(status_code=400, detail=resultado)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        log.error("Erro na thread /remover-banner-corrida: %s", e)
        raise HTTPException(
            status_code=500, detail={"sucesso": False, "mensagem": f"Erro interno: {e}"}
        )


@app.post("/remover-anuncio-passageiro", response_model=ResultadoOutput)
async def remover_anuncio_passageiro_endpoint(creds: RemoverAnuncioInput):
    """
    Remove anúncios de passageiro na tela inicial do app.
    Envie `indice` igual ao `dom_slot_idx` devolvido no disparo (sufixo da linha no painel) ou posição
    na lista ordenada (0 = primeiro). Omita `indice` para remover todas.
    """
    log.info(
        "Requisição (remover-anuncio-passageiro) para: %s indice=%s",
        creds.email,
        creds.indice,
    )

    from functools import partial
    loop = asyncio.get_event_loop()
    func = partial(
        executar_remover_anuncio_passageiro,
        email=creds.email,
        senha=creds.senha,
        chave_secreta=creds.chave_secreta,
        headless=creds.headless,
        manter_aberto=creds.manter_aberto,
        indice=creds.indice,
    )

    try:
        resultado = await loop.run_in_executor(executor, func)
        if "chave_totp" not in resultado or resultado["chave_totp"] is None:
            resultado["chave_totp"] = ""
        if not resultado.get("sucesso", False):
            raise HTTPException(status_code=400, detail=resultado)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        log.error("Erro na thread /remover-anuncio-passageiro: %s", e)
        raise HTTPException(
            status_code=500, detail={"sucesso": False, "mensagem": f"Erro interno: {e}"}
        )

@app.get("/")
async def root():
    """Evita 404 no path raiz (testes rápidos no painel / N8N)."""
    return {"ok": True, "service": "taximachine-automacao", "health": "/health", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok"}


def _notificacao_http_erro(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"sucesso": False, "mensagem": str(exc)},
    )


def _resolve_chave_acao(session_token: str, chave_secreta: Optional[str]) -> Optional[str]:
    if chave_secreta:
        return chave_secreta
    email = get_session_email(session_token)
    if email:
        return obter_chave(email)
    return None


def _require_session(token: str):
    http = get_session(token)
    if not http:
        raise HTTPException(
            status_code=401,
            detail={
                "sucesso": False,
                "mensagem": "session_token inválido ou expirado. Faça login novamente.",
            },
        )
    return http


def _campanha_form(inp: NotificacaoCampanhaInput):
    return form_from_dict(inp.model_dump())


@app.post("/notificacao/login")
async def notificacao_login_http(inp: NotificacaoLoginInput):
    """
    Login HTTP no painel TaxiMachine (sem Selenium).

    Retorna `session_token` para usar em GET/POST /notificacao/categorias.
    Sessão válida por ~30 minutos no servidor.
    """
    log.info("POST /notificacao/login email=%s", inp.email)
    loop = asyncio.get_event_loop()
    try:
        resultado = await loop.run_in_executor(
            executor,
            lambda: login_painel(
                email=inp.email,
                senha=inp.senha,
                codigo_2fa=inp.codigo_2fa,
                chave_secreta=inp.chave_secreta,
                gerar_codigo_fn=gerar_codigo,
            ),
        )
        return resultado
    except Exception as e:
        log.warning("Falha /notificacao/login para %s: %s", inp.email, e)
        raise _notificacao_http_erro(e)


@app.get("/notificacao/categorias")
async def notificacao_categorias_get(
    session_token: str,
    bandeira_id: Optional[str] = None,
):
    """
    Lista categorias de motoristas para filtros de notificação em massa.

    Use o `session_token` retornado por POST /notificacao/login.
    """
    log.info(
        "GET /notificacao/categorias session_token=%s bandeira_id=%s",
        session_token[:8] + "...",
        bandeira_id,
    )
    http = get_session(session_token)
    if not http:
        raise HTTPException(
            status_code=401,
            detail={
                "sucesso": False,
                "mensagem": "session_token inválido ou expirado. Faça login novamente.",
            },
        )

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: obter_categorias(http, bandeira_id=bandeira_id),
        )
    except Exception as e:
        log.warning("Falha GET /notificacao/categorias: %s", e)
        raise _notificacao_http_erro(e)


@app.post("/notificacao/categorias")
async def notificacao_categorias_post(inp: NotificacaoCategoriasInput):
    """
    Lista categorias usando `session_token` **ou** `email` + `senha` (login automático).
    """
    log.info(
        "POST /notificacao/categorias session=%s email=%s bandeira_id=%s",
        bool(inp.session_token),
        inp.email,
        inp.bandeira_id,
    )
    loop = asyncio.get_event_loop()
    try:
        if inp.session_token:
            http = get_session(inp.session_token)
            if not http:
                raise RuntimeError("session_token inválido ou expirado. Faça login novamente.")
            result = await loop.run_in_executor(
                executor,
                lambda: obter_categorias(http, bandeira_id=inp.bandeira_id),
            )
            result["session_token"] = inp.session_token
            return result

        if not inp.email or not inp.senha:
            raise RuntimeError("Informe session_token ou email + senha.")

        return await loop.run_in_executor(
            executor,
            lambda: obter_categorias_com_credenciais(
                email=inp.email,
                senha=inp.senha,
                bandeira_id=inp.bandeira_id,
                codigo_2fa=inp.codigo_2fa,
                chave_secreta=inp.chave_secreta,
                gerar_codigo_fn=gerar_codigo,
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Falha POST /notificacao/categorias: %s", e)
        raise _notificacao_http_erro(e)


@app.get("/notificacao/bandeiras")
async def notificacao_bandeiras_get(session_token: str):
    http = _require_session(session_token)
    loop = asyncio.get_event_loop()
    try:
        bandeiras = await loop.run_in_executor(executor, lambda: obter_bandeiras(http))
        return {"sucesso": True, "bandeiras": bandeiras, "total": len(bandeiras)}
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.post("/notificacao/filtrar")
async def notificacao_filtrar(inp: NotificacaoCampanhaInput):
    http = _require_session(inp.session_token)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: filtrar_destinatarios(http, _campanha_form(inp)),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.get("/notificacao/status/{report_id}")
async def notificacao_status(report_id: int, session_token: str):
    http = _require_session(session_token)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: verificar_status_relatorio(http, report_id),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.get("/notificacao/total/{report_id}")
async def notificacao_total(report_id: int, session_token: str):
    http = _require_session(session_token)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: obter_total_destinatarios(http, report_id),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.post("/notificacao/aguardar")
async def notificacao_aguardar(inp: NotificacaoAguardarInput):
    http = _require_session(inp.session_token)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: aguardar_relatorio(
                http, inp.report_id, timeout_seg=inp.timeout_seg
            ),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.post("/notificacao/enviar")
async def notificacao_enviar(inp: NotificacaoCampanhaInput):
    http = _require_session(inp.session_token)
    chave = _resolve_chave_acao(inp.session_token, inp.chave_secreta)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: enviar_ou_agendar(
                http,
                _campanha_form(inp),
                agendar=False,
                codigo_2fa_acao=inp.codigo_2fa_acao,
                chave_secreta=chave,
                gerar_codigo_fn=gerar_codigo,
            ),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.post("/notificacao/agendar")
async def notificacao_agendar(inp: NotificacaoAgendarInput):
    http = _require_session(inp.session_token)
    chave = _resolve_chave_acao(inp.session_token, inp.chave_secreta)
    form = _campanha_form(inp)
    form.data_envio = inp.data_envio
    form.hora_envio = inp.hora_envio
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: enviar_ou_agendar(
                http,
                form,
                agendar=True,
                codigo_2fa_acao=inp.codigo_2fa_acao,
                chave_secreta=chave,
                gerar_codigo_fn=gerar_codigo,
            ),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.post("/notificacao/autenticar-acao")
async def notificacao_autenticar_acao(inp: NotificacaoAutenticarAcaoInput):
    http = _require_session(inp.session_token)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: autenticar_acao_2fa(
                http,
                codigo_2fa=inp.codigo_2fa,
                chave_secreta=inp.chave_secreta,
                gerar_codigo_fn=gerar_codigo,
            ),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.get("/notificacao/listar")
async def notificacao_listar(session_token: str, pagina: int = 1):
    http = _require_session(session_token)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: listar_notificacoes(http, pagina=pagina),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.delete("/notificacao/{notificacao_id}")
async def notificacao_cancelar(
    notificacao_id: int,
    session_token: str,
    destinatario: str = "D",
):
    http = _require_session(session_token)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            executor,
            lambda: cancelar_notificacao(http, notificacao_id, destinatario),
        )
    except Exception as e:
        raise _notificacao_http_erro(e)


@app.post("/financeiro_completo_02")
async def financeiro_completo_02(inp: FinanceiroCompleto02Input):
    """
    Executa o fluxo financeiro completo (TaxiMachine): login + 2FA, filtro mês passado,
    ganhos gerais, página de taxas/créditos. Grava `dados_financeiro_completo.json` no disco do servidor.

    - Default: **não** envia webhook (`enviar_webhook=false`).
    - Com `manter_aberto=true` (default): roda em **thread em segundo plano** e a API responde na hora
      (o Chrome fica aberto no host até você fechar o processo ou a janela).
    - Com `manter_aberto=false`: aguarda o fim do fluxo e devolve JSON com `dados_extraidos` na resposta
      (encerra o navegador ao terminar).
    """
    from fluxo_financeiro_completo import DADOS_FILE, executar_fluxo_financeiro_completo

    log.info(
        "POST /financeiro_completo_02 email=%s headless=%s manter_aberto=%s webhook=%s",
        inp.email,
        inp.headless,
        inp.manter_aberto,
        inp.enviar_webhook,
    )

    if inp.manter_aberto:

        def _run():
            executar_fluxo_financeiro_completo(
                inp.email,
                inp.senha,
                headless=inp.headless,
                no_wait=False,
                enviar_webhook=inp.enviar_webhook,
            )

        threading.Thread(target=_run, daemon=True).start()
        return {
            "sucesso": True,
            "mensagem": "Fluxo financeiro iniciado em segundo plano. Navegador permanece aberto no servidor.",
            "email": inp.email,
            "manter_aberto": True,
            "enviar_webhook": inp.enviar_webhook,
            "arquivo_json": os.path.basename(DADOS_FILE),
            "endpoint": "/financeiro_completo_02",
        }

    loop = asyncio.get_event_loop()
    resultado = await loop.run_in_executor(
        executor,
        lambda: executar_fluxo_financeiro_completo(
            inp.email,
            inp.senha,
            headless=inp.headless,
            no_wait=True,
            enviar_webhook=inp.enviar_webhook,
        ),
    )

    if not resultado:
        raise HTTPException(
            status_code=500,
            detail={"sucesso": False, "mensagem": "Retorno vazio do fluxo financeiro."},
        )
    # HTTP 200 mesmo com sucesso=false — n8n e webhooks tratam pelo campo "sucesso" no JSON.
    return resultado


@app.post("/financeiro_historico_corridas")
async def financeiro_historico_corridas(inp: FinanceiroHistoricoCorridasInput):
    """
    Login (2FA/setup se preciso) → `historicoCorridas2` → painel **Filtro** → datas do **mês anterior**
    (00:00–23:59) → **Filtrar** → aguarda → lê total `Exibindo 1-30 de N resultados` → `/bandeira/creditos`
    → taxa central e taxa seguro app → JSON + webhook (URL padrão de teste ou `webhook_url`).
    """
    from fluxo_historico_corridas_taxas import (
        DADOS_FILE as HISTORICO_DADOS_FILE,
        executar_fluxo_historico_corridas_taxas,
    )

    log.info(
        "POST /financeiro_historico_corridas email=%s headless=%s manter_aberto=%s webhook=%s",
        inp.email,
        inp.headless,
        inp.manter_aberto,
        inp.enviar_webhook,
    )

    if inp.manter_aberto:

        def _run():
            executar_fluxo_historico_corridas_taxas(
                inp.email,
                inp.senha,
                headless=inp.headless,
                no_wait=False,
                enviar_webhook=inp.enviar_webhook,
                webhook_url=inp.webhook_url,
            )

        threading.Thread(target=_run, daemon=True).start()
        return {
            "sucesso": True,
            "mensagem": "Fluxo histórico + taxas iniciado em segundo plano.",
            "email": inp.email,
            "manter_aberto": True,
            "enviar_webhook": inp.enviar_webhook,
            "arquivo_json": os.path.basename(HISTORICO_DADOS_FILE),
            "endpoint": "/financeiro_historico_corridas",
        }

    loop = asyncio.get_event_loop()
    resultado = await loop.run_in_executor(
        executor,
        lambda: executar_fluxo_historico_corridas_taxas(
            inp.email,
            inp.senha,
            headless=inp.headless,
            no_wait=True,
            enviar_webhook=inp.enviar_webhook,
            webhook_url=inp.webhook_url,
        ),
    )

    if resultado is None:
        raise HTTPException(
            status_code=500,
            detail={"sucesso": False, "mensagem": "Retorno vazio do fluxo histórico."},
        )
    return resultado


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
