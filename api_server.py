"""
API FastAPI para disparar a automação de 2FA via HTTP POST.

Uso:
    uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    POST /autenticar  - Setup 2FA inicial (ou login se já configurado)
    POST /login       - Login com 2FA já configurado (usa chave salva)
    GET  /chaves      - Lista emails com chave TOTP salva
    POST /codigo      - Gera código TOTP para um email
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from auto_2fa import executar_automacao, executar_login, executar_login_recursos_premium, executar_adicionar_anuncio_motorista, carregar_chaves, obter_chave, gerar_codigo
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import base64
import uuid
import tempfile
import os
import urllib.request

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
            req = urllib.request.Request(input_data.imagem_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(tmp_imagem_path, 'wb') as out_file:
                out_file.write(response.read())
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


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
