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
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from auto_2fa import executar_automacao, executar_login, executar_login_recursos_premium, executar_adicionar_anuncio_motorista, executar_remover_anuncio_motorista, executar_adicionar_anuncio_passageiro, executar_remover_anuncio_passageiro, carregar_chaves, obter_chave, gerar_codigo
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import base64
import uuid
import tempfile
import os
import urllib.request
from typing import Any, Dict, Optional

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


class ResultadoOutput(BaseModel):
    sucesso: bool
    email: str
    chave_totp: str
    mensagem: str
    verificacao: Optional[Dict[str, Any]] = None


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
