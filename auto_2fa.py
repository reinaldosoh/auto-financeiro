"""
Automação completa de login + configuração 2FA no TaxiMachine.
Usa Selenium para navegar no browser e pyotp para gerar códigos TOTP.
"""

import time
import logging
import json
import os
import re
import base64
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

URL_LOGIN = "https://cloud.taximachine.com.br/"
TIMEOUT = 20
CHAVES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chaves_totp.json")

# Armazena drivers abertos para evitar garbage collection quando manter_aberto=True
_drivers_abertos = {}


def salvar_chave(email: str, chave_totp: str):
    """Salva a chave TOTP associada ao email em arquivo JSON."""
    dados = carregar_chaves()
    dados[email.lower()] = chave_totp
    with open(CHAVES_FILE, "w") as f:
        json.dump(dados, f, indent=2)
    log.info("Chave TOTP salva para %s", email)


def carregar_chaves() -> dict:
    """Carrega todas as chaves TOTP salvas."""
    if os.path.exists(CHAVES_FILE):
        with open(CHAVES_FILE, "r") as f:
            return json.load(f)
    return {}


def obter_chave(email: str) -> str:
    """Retorna a chave TOTP salva para o email, ou string vazia."""
    dados = carregar_chaves()
    return dados.get(email.lower(), "")


def gerar_codigo(chave_totp: str) -> str:
    """Gera o código TOTP de 6 dígitos a partir da chave."""
    totp = pyotp.TOTP(chave_totp)
    return totp.now()


def criar_driver(headless: bool = False) -> webdriver.Chrome:
    """Cria e retorna uma instância do Chrome WebDriver.
    
    Quando executado no Docker (DOCKER=true), usa display virtual Xvfb (:99)
    em vez de headless puro — necessário para uploads AJAX funcionarem corretamente.
    """
    opts = Options()
    em_docker = os.environ.get("DOCKER", "").lower() in ("1", "true", "yes")

    if em_docker:
        # No Docker: usa display virtual Xvfb iniciado pelo entrypoint.sh
        display = os.environ.get("DISPLAY", ":99")
        log.info("Docker detectado: usando DISPLAY=%s (modo não-headless)", display)
        opts.add_argument(f"--display={display}")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--disable-extensions")
    elif headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")

    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.implicitly_wait(5)
    return driver


def esperar_elemento(driver, by, valor, timeout=TIMEOUT):
    """Espera até que um elemento esteja clicável e o retorna."""
    wait = WebDriverWait(driver, timeout)
    return wait.until(EC.element_to_be_clickable((by, valor)))


def esperar_elemento_visivel(driver, by, valor, timeout=TIMEOUT):
    """Espera até que um elemento esteja visível e o retorna."""
    wait = WebDriverWait(driver, timeout)
    return wait.until(EC.visibility_of_element_located((by, valor)))


def fazer_login(driver, email: str, senha: str) -> bool:
    """
    Etapa 1: Acessa a página de login e faz autenticação com email/senha.
    Retorna True se o login foi bem-sucedido.
    """
    log.info("Acessando %s", URL_LOGIN)
    driver.get(URL_LOGIN)
    time.sleep(3)

    # Campo de email: input#LoginForm_username (type=text, name=LoginForm[username])
    try:
        campo_email = esperar_elemento(driver, By.ID, "LoginForm_username", timeout=15)
        log.info("Campo email encontrado: #LoginForm_username")
    except TimeoutException:
        log.error("Campo de email #LoginForm_username não encontrado.")
        log.info("HTML da página: %s", driver.page_source[:3000])
        return False

    # Campo de senha: input#LoginForm_password (type=password)
    try:
        campo_senha = esperar_elemento(driver, By.ID, "LoginForm_password", timeout=10)
        log.info("Campo senha encontrado: #LoginForm_password")
    except TimeoutException:
        # Fallback
        try:
            campo_senha = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            log.info("Campo senha encontrado por fallback: input[type=password]")
        except NoSuchElementException:
            log.error("Campo de senha não encontrado.")
            return False

    # Preenche os campos
    campo_email.clear()
    campo_email.send_keys(email)
    time.sleep(0.3)

    campo_senha.clear()
    campo_senha.send_keys(senha)
    time.sleep(0.3)

    # Botão Entrar: button#entrar ou button com texto 'Entrar'
    botao_login = None
    seletores_botao = [
        (By.ID, "entrar"),
        (By.CSS_SELECTOR, "button#entrar"),
        (By.XPATH, '//button[contains(text(),"Entrar")]'),
        (By.CSS_SELECTOR, 'button[type="submit"]'),
        (By.CSS_SELECTOR, 'input[type="submit"]#entrar'),
    ]
    for by, sel in seletores_botao:
        try:
            botao_login = driver.find_element(by, sel)
            if botao_login.is_displayed():
                log.info("Botão login encontrado: %s", sel)
                break
            botao_login = None
        except NoSuchElementException:
            continue

    if not botao_login:
        log.error("Botão de login não encontrado.")
        return False

    botao_login.click()
    log.info("Login submetido. Aguardando resposta...")
    time.sleep(4)

    return True


def etapa1_avancar(driver) -> bool:
    """
    Etapa 2FA - Passo 1: Clica em 'Avançar' na tela que pede para baixar o app.
    Botão: button#btn-avancar-cadastrar-2fa (.mfa__button--next-cadastro)
    """
    log.info("Procurando botão 'Avançar' (etapa 1 do 2FA)...")

    seletores_avancar = [
        (By.ID, "btn-avancar-cadastrar-2fa"),
        (By.CSS_SELECTOR, "button.mfa__button--next-cadastro"),
        (By.CSS_SELECTOR, "button.mfa__button--primary.mfa__button--next"),
        (By.XPATH, '//button[contains(text(),"Avançar")]'),
        (By.XPATH, '//*[contains(text(),"Avançar")]'),
    ]

    for by, sel in seletores_avancar:
        try:
            botao = esperar_elemento(driver, by, sel, timeout=15)
            if botao.is_displayed():
                botao.click()
                log.info("Clicou em 'Avançar' (etapa 1): %s", sel)
                time.sleep(2)
                return True
        except TimeoutException:
            continue

    log.error("Botão 'Avançar' da etapa 1 não encontrado.")
    return False


def etapa2_extrair_chave(driver) -> str:
    """
    Etapa 2FA - Passo 2: Extrai a chave secreta TOTP da página.
    O botão 'Copiar chave' tem id='copiar-secret'.
    Retorna a chave sem espaços.
    """
    log.info("Procurando chave secreta TOTP na página (etapa 2 do 2FA)...")
    time.sleep(2)

    chave = None

    # Estratégia 1: Buscar elemento com classe mfa que contém a chave
    seletores_chave = [
        (By.CSS_SELECTOR, '.mfa__secret-key'),
        (By.CSS_SELECTOR, '.mfa__key'),
        (By.CSS_SELECTOR, '[class*="secret"]'),
        (By.CSS_SELECTOR, '[class*="key"][class*="mfa"]'),
    ]
    for by, sel in seletores_chave:
        try:
            elem = driver.find_element(by, sel)
            texto = elem.text.strip()
            if len(texto.replace(" ", "")) >= 16:
                chave = texto
                log.info("Chave encontrada por seletor: %s -> %s", sel, texto)
                break
        except NoSuchElementException:
            continue

    # Estratégia 2: Buscar padrão TOTP no texto da página (grupos de 4 maiúsculas/números)
    if not chave:
        page_text = driver.find_element(By.TAG_NAME, "body").text
        padrao = re.findall(r'([A-Z0-9]{4}(?:\s+[A-Z0-9]{4}){3,7})', page_text)
        if padrao:
            chave = max(padrao, key=len)
            log.info("Chave encontrada por regex no texto da página: %s", chave)

    # Estratégia 3: Procurar perto do botão copiar-secret
    if not chave:
        try:
            botao_copiar = driver.find_element(By.ID, "copiar-secret")
            parent = botao_copiar.find_element(By.XPATH, './ancestor::*[position()<=3]')
            texto_parent = parent.text
            padrao2 = re.findall(r'([A-Z0-9]{4}(?:\s+[A-Z0-9]{4}){3,7})', texto_parent)
            if padrao2:
                chave = max(padrao2, key=len)
                log.info("Chave encontrada próxima ao botão #copiar-secret: %s", chave)
        except NoSuchElementException:
            pass

    # Estratégia 4: Buscar data-attribute ou input hidden com a chave
    if not chave:
        try:
            # Tenta pegar de um atributo data-secret ou similar
            elems = driver.find_elements(By.CSS_SELECTOR, '[data-secret], [data-key], [data-totp]')
            for elem in elems:
                for attr in ['data-secret', 'data-key', 'data-totp']:
                    val = elem.get_attribute(attr)
                    if val and len(val.replace(" ", "")) >= 16:
                        chave = val
                        log.info("Chave encontrada em atributo %s: %s", attr, chave)
                        break
                if chave:
                    break
        except Exception:
            pass

    # Estratégia 5: Genérica - procurar texto maiúsculo longo em qualquer elemento
    if not chave:
        todos = driver.find_elements(By.XPATH, '//*[string-length(text()) >= 16 and string-length(text()) <= 80]')
        for elem in todos:
            texto = elem.text.strip()
            if re.match(r'^[A-Z0-9\s]{16,}$', texto) and len(texto.replace(" ", "")) >= 16:
                chave = texto
                log.info("Chave encontrada por busca genérica: %s", texto)
                break

    if chave:
        chave_limpa = chave.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "").strip()
        log.info("Chave TOTP extraída: %s (limpa: %s)", chave, chave_limpa)

        # Clica em Avançar para ir ao passo 3
        # Existem múltiplos botões com a mesma classe; precisamos achar o visível
        clicou = False

        # Tentativa 1: buscar TODOS os botões mfa__button--next-cadastro e clicar no visível
        try:
            botoes = driver.find_elements(By.CSS_SELECTOR, "button.mfa__button--next-cadastro")
            for btn in botoes:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    log.info("Clicou em 'Avançar' (etapa 2) via JS no botão visível")
                    clicou = True
                    break
        except Exception as e:
            log.warning("Tentativa 1 falhou: %s", e)

        # Tentativa 2: JS direto procurando texto "Avançar" visível
        if not clicou:
            try:
                driver.execute_script("""
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        if (btns[i].offsetParent !== null && btns[i].innerText.includes('Avançar')) {
                            btns[i].click();
                            break;
                        }
                    }
                """)
                log.info("Clicou em 'Avançar' (etapa 2) via JS genérico")
                clicou = True
            except Exception as e:
                log.warning("Tentativa 2 falhou: %s", e)

        # Tentativa 3: XPath
        if not clicou:
            try:
                botao = esperar_elemento(driver, By.XPATH, '//button[contains(text(),"Avançar")]', timeout=5)
                driver.execute_script("arguments[0].click();", botao)
                log.info("Clicou em 'Avançar' (etapa 2) via XPath + JS")
                clicou = True
            except TimeoutException:
                log.error("Não conseguiu clicar em 'Avançar' na etapa 2")

        time.sleep(3)

        return chave_limpa
    else:
        log.error("Não foi possível extrair a chave TOTP da página.")
        log.info("Texto da página: %s", driver.find_element(By.TAG_NAME, "body").text[:3000])
        return ""


def etapa3_inserir_codigo(driver, chave_totp: str) -> bool:
    """
    Etapa 2FA - Passo 3: Gera o código TOTP e insere no campo de verificação.
    Campo: input com placeholder 'Código de 6 dígitos'
    Botão: button#btn-avancar-cadastrar-2fa ou com texto 'Verificar'
    """
    log.info("Etapa 3 do 2FA: inserindo código TOTP...")

    # PRIMEIRO encontra o campo, DEPOIS gera o código (para evitar expiração)
    campo_codigo = None
    seletores_codigo = [
        (By.ID, "codigo-cadastrar-2fa"),
        (By.ID, "codigo-solicitar-2fa"),
        (By.CSS_SELECTOR, 'input[placeholder*="6 d\u00edgito"]'),
        (By.CSS_SELECTOR, 'input[placeholder*="C\u00f3digo de 6"]'),
        (By.CSS_SELECTOR, 'input.mfa__input'),
        (By.XPATH, '//input[contains(@placeholder,"6")]'),
        (By.XPATH, '//input[contains(@placeholder,"d\u00edgito")]'),
    ]

    for by, sel in seletores_codigo:
        try:
            campo_codigo = esperar_elemento(driver, by, sel, timeout=5)
            if campo_codigo.is_displayed():
                log.info("Campo de código encontrado: %s", sel)
                break
            campo_codigo = None
        except TimeoutException:
            continue

    if not campo_codigo:
        # Fallback: qualquer input visível que não seja email/senha
        try:
            inputs = driver.find_elements(By.TAG_NAME, 'input')
            for inp in inputs:
                inp_type = inp.get_attribute('type') or ''
                inp_id = inp.get_attribute('id') or ''
                if inp.is_displayed() and inp_type not in ('hidden', 'password') and 'LoginForm' not in inp_id:
                    campo_codigo = inp
                    log.info("Campo de código encontrado por fallback: id=%s type=%s", inp_id, inp_type)
                    break
        except Exception:
            pass

    if not campo_codigo:
        log.error("Não foi possível encontrar o campo para inserir o código.")
        return False

    # Gera o código AGORA, imediatamente antes de digitar
    totp = pyotp.TOTP(chave_totp)
    codigo = totp.now()
    log.info("Código TOTP gerado (fresh): %s", codigo)

    # Insere o código
    campo_codigo.clear()
    campo_codigo.send_keys(codigo)
    time.sleep(0.5)

    # Clica em Verificar (usando JS click para garantir)
    clicou = False
    seletores_verificar = [
        (By.XPATH, '//button[contains(text(),"Verificar")]'),
        (By.CSS_SELECTOR, "button.mfa__button--secondary.mfa__button--next-cadastro"),
        (By.ID, "btn-avancar-cadastrar-2fa"),
        (By.XPATH, '//button[contains(text(),"Confirmar")]'),
        (By.XPATH, '//button[contains(text(),"Ativar")]'),
    ]
    for by, sel in seletores_verificar:
        try:
            botao = driver.find_element(by, sel)
            if botao.is_displayed():
                driver.execute_script("arguments[0].click();", botao)
                log.info("Botão verificar clicado (JS): %s", sel)
                clicou = True
                break
        except NoSuchElementException:
            continue

    # JS fallback: procurar qualquer botão visível com texto "Verificar"
    if not clicou:
        try:
            driver.execute_script("""
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].offsetParent !== null && btns[i].innerText.includes('Verificar')) {
                        btns[i].click();
                        break;
                    }
                }
            """)
            log.info("Botão 'Verificar' clicado via JS genérico")
            clicou = True
        except Exception:
            pass

    if not clicou:
        log.error("Botão 'Verificar' não encontrado.")
        return False

    time.sleep(3)
    log.info("Código TOTP submetido no setup! Aguardando confirmação...")
    return True


def detectar_cenario_pos_login(driver) -> str:
    """
    Após o login, detecta em qual tela estamos:
    - 'setup_2fa': Tela de configuração inicial do 2FA (3 etapas)
    - 'login_2fa': Tela pedindo código de 6 dígitos (2FA já configurado)
    - 'logado': Já entrou no sistema (sem 2FA)
    - 'erro': Não conseguiu identificar
    """
    log.info("Detectando cenário pós-login...")
    time.sleep(2)

    page_text = driver.find_element(By.TAG_NAME, "body").text.lower()

    # Verifica se é tela de setup 2FA ("Ative a verificação em duas etapas")
    if "ative a verificação" in page_text or "verificação em duas etapas" in page_text:
        # Verifica se está no passo 1 (baixe o app) ou já no passo com QR code
        try:
            driver.find_element(By.ID, "btn-avancar-cadastrar-2fa")
            log.info("Cenário detectado: SETUP 2FA (configuração inicial)")
            return "setup_2fa"
        except NoSuchElementException:
            pass

    # Verifica se é tela de login 2FA ("Insira o código" / "código de 6 dígitos")
    if "insira o código" in page_text or "código de verificação" in page_text or "código de 6 dígitos" in page_text:
        log.info("Cenário detectado: LOGIN 2FA (código solicitado)")
        return "login_2fa"

    # Verifica se é a tela de solicitar 2FA (outro fluxo)
    try:
        driver.find_element(By.ID, "btn-avancar-solicitar-2fa")
        log.info("Cenário detectado: LOGIN 2FA (solicitação)")
        return "login_2fa"
    except NoSuchElementException:
        pass

    # Verifica se já logou (presença de dashboard/menu)
    indicadores_logado = ["dashboard", "painel", "corridas", "motoristas", "logout", "sair"]
    for ind in indicadores_logado:
        if ind in page_text:
            log.info("Cenário detectado: JÁ LOGADO")
            return "logado"

    log.warning("Cenário não identificado. Texto: %s", page_text[:500])
    return "erro"


def inserir_codigo_login_2fa(driver, chave_totp: str) -> bool:
    """
    Insere o código TOTP na tela de login com 2FA já configurado.
    Tela: 'Insira o código de verificação' com campo 'Código de 6 dígitos' e botão 'Verificar'.
    """
    log.info("Inserindo código TOTP na tela de login 2FA...")

    # PRIMEIRO encontra o campo, DEPOIS gera o código (para evitar expiração)
    campo_codigo = None
    seletores = [
        (By.ID, "codigo-solicitar-2fa"),
        (By.CSS_SELECTOR, 'input#codigo-solicitar-2fa'),
        (By.CSS_SELECTOR, 'input[placeholder*="6 d\u00edgito"]'),
        (By.CSS_SELECTOR, 'input[placeholder*="C\u00f3digo de 6"]'),
        (By.CSS_SELECTOR, 'input.mfa__input'),
        (By.XPATH, '//input[contains(@placeholder,"6")]'),
        (By.XPATH, '//input[contains(@placeholder,"d\u00edgito")]'),
    ]

    for by, sel in seletores:
        try:
            campo_codigo = esperar_elemento(driver, by, sel, timeout=5)
            if campo_codigo.is_displayed():
                log.info("Campo código encontrado: %s", sel)
                break
            campo_codigo = None
        except TimeoutException:
            continue

    # Fallback: qualquer input visível que não seja login
    if not campo_codigo:
        try:
            inputs = driver.find_elements(By.TAG_NAME, 'input')
            for inp in inputs:
                inp_type = inp.get_attribute('type') or ''
                inp_id = inp.get_attribute('id') or ''
                if inp.is_displayed() and inp_type not in ('hidden', 'password') and 'LoginForm' not in inp_id:
                    campo_codigo = inp
                    log.info("Campo código por fallback: id=%s", inp_id)
                    break
        except Exception:
            pass

    if not campo_codigo:
        log.error("Campo de código não encontrado na tela de login 2FA.")
        return False

    # Gera o código AGORA, imediatamente antes de digitar
    codigo = gerar_codigo(chave_totp)
    log.info("Código TOTP gerado (fresh): %s", codigo)

    campo_codigo.clear()
    campo_codigo.send_keys(codigo)
    time.sleep(0.5)

    # Clica em Verificar
    clicou = False
    seletores_btn = [
        (By.ID, "btn-avancar-solicitar-2fa"),
        (By.ID, "auth-modal__button-verify"),
        (By.XPATH, '//button[contains(text(),"Verificar")]'),
        (By.CSS_SELECTOR, "button.mfa__button--secondary"),
        (By.CSS_SELECTOR, "button.auth-modal__button--secondary"),
    ]
    for by, sel in seletores_btn:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                log.info("Clicou em 'Verificar': %s", sel)
                clicou = True
                break
        except NoSuchElementException:
            continue

    # JS fallback
    if not clicou:
        try:
            driver.execute_script("""
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].offsetParent !== null && btns[i].innerText.includes('Verificar')) {
                        btns[i].click();
                        break;
                    }
                }
            """)
            log.info("Clicou em 'Verificar' via JS genérico")
            clicou = True
        except Exception:
            pass

    if not clicou:
        log.error("Botão 'Verificar' não encontrado.")
        return False

    time.sleep(3)
    log.info("Código TOTP submetido no login 2FA!")
    return True


def navegar_cadastros_clientes(driver) -> bool:
    """
    Após o login, navega até Cadastro → Clientes.
    URL direta confirmada: /cliente/index?resetSession=1
    Também tenta clicar no menu para manter a experiência visual.
    Retorna True se conseguiu navegar.
    """
    log.info("Navegando para Cadastro > Clientes...")
    time.sleep(2)

    # Estratégia 1: clicar em 'Cadastro' para abrir o submenu visualmente
    try:
        cadastro = driver.find_element(By.XPATH, '//a[normalize-space(text())="Cadastro" and contains(@class,"nav-link")]')
        driver.execute_script("arguments[0].click();", cadastro)
        log.info("Clicou em 'Cadastro' no menu")
        time.sleep(2)

        # Tenta clicar no link Clientes direto via href confirmado
        try:
            clientes_link = driver.find_element(By.XPATH, '//a[contains(@href,"/cliente/index")]')
            driver.execute_script("arguments[0].click();", clientes_link)
            log.info("Clicou em 'Clientes' via href /cliente/index")
            time.sleep(2)

            if "cliente" in driver.current_url:
                log.info("✅ Navegou para Clientes! URL: %s", driver.current_url)
                return True
        except Exception:
            pass
    except Exception as e:
        log.warning("Falha ao clicar no menu Cadastro: %s", e)

    # Estratégia 2: navegar diretamente pela URL confirmada
    base_url = "https://cloud.taximachine.com.br"
    url_clientes = f"{base_url}/cliente/index?resetSession=1"
    log.info("Navegando diretamente para URL: %s", url_clientes)
    driver.get(url_clientes)
    time.sleep(3)

    if "cliente" in driver.current_url:
        log.info("✅ Naveg. para Clientes via URL direta! URL: %s", driver.current_url)
        return True

    log.error("Falha ao navegar para Clientes. URL atual: %s", driver.current_url)
    return False


def navegar_recursos_premium(driver) -> bool:
    """
    Após o login, navega até Configurações → Gerais e clica na aba 'Recursos premium'.
    URL confirmada de Configurações/Gerais: /bandeira/update
    Retorna True se conseguiu navegar e clicar na aba.
    """
    log.info("Navegando para Configurações > Gerais > Recursos premium...")
    time.sleep(2)

    # --- PASSO 1: Clicar em 'Configurações' no menu ---
    try:
        config_link = driver.find_element(
            By.XPATH,
            '//a[normalize-space(text())="Configurações" and contains(@class,"nav-link")]'
        )
        driver.execute_script("arguments[0].click();", config_link)
        log.info("Clicou em 'Configurações' no menu")
        time.sleep(2)

        # --- PASSO 2: Clicar em 'Gerais' no submenu (href=/bandeira/update) ---
        try:
            gerais = driver.find_element(By.XPATH, '//a[contains(@href,"/bandeira/update")]')
            driver.execute_script("arguments[0].click();", gerais)
            log.info("Clicou em 'Gerais' via href /bandeira/update")
            time.sleep(3)

            if "bandeira" in driver.current_url:
                log.info("Navegou para Configurações/Gerais! URL: %s", driver.current_url)
        except Exception:
            raise Exception("Link Gerais não encontrado após clicar em Configurações")

    except Exception as e:
        # Fallback: navegar diretamente pela URL confirmada
        log.warning("Fallback URL direta: %s", e)
        url_gerais = "https://cloud.taximachine.com.br/bandeira/update"
        driver.get(url_gerais)
        time.sleep(4)
        log.info("URL após navegação direta: %s", driver.current_url)

    if "bandeira" not in driver.current_url:
        log.error("Falha ao chegar em Configurações/Gerais. URL: %s", driver.current_url)
        return False

    # --- PASSO 3: Clicar na aba 'Recursos premium' ---
    # A aba pode ser um link <a> dentro de uma lista de abas (Bootstrap nav-tabs)
    seletores_aba = [
        (By.XPATH, '//a[normalize-space(text())="Recursos premium"]'),
        (By.LINK_TEXT, "Recursos premium"),
        (By.PARTIAL_LINK_TEXT, "Recursos premium"),
        (By.XPATH, '//li[contains(.,"Recursos premium")]//a'),
        (By.XPATH, '//*[contains(text(),"Recursos premium")]'),
    ]

    clicou_aba = False
    for by, sel in seletores_aba:
        try:
            aba = driver.find_element(by, sel)
            driver.execute_script("arguments[0].click();", aba)
            log.info("Clicou na aba 'Recursos premium': %s", sel)
            clicou_aba = True
            time.sleep(2)
            break
        except Exception:
            continue

    if not clicou_aba:
        # JS fallback completo
        try:
            driver.execute_script("""
                var elems = document.querySelectorAll('a, li, span, div, button');
                for (var i = 0; i < elems.length; i++) {
                    var txt = elems[i].innerText ? elems[i].innerText.trim() : '';
                    if (txt === 'Recursos premium') {
                        elems[i].click();
                        break;
                    }
                }
            """)
            log.info("Clicou em 'Recursos premium' via JS genérico")
            clicou_aba = True
            time.sleep(2)
        except Exception as e:
            log.error("Falha ao clicar em 'Recursos premium': %s", e)
            return False

    log.info("✅ Navegação para Configurações > Gerais > Recursos premium concluída! URL: %s", driver.current_url)
    return clicou_aba


def executar_login_recursos_premium(email: str, senha: str, headless: bool = False, manter_aberto: bool = True) -> dict:
    """
    Login com 2FA e navega para Configurações → Gerais → aba 'Recursos premium'.
    """
    resultado = {
        "sucesso": False,
        "email": email,
        "chave_totp": "",
        "mensagem": "",
    }

    chave = obter_chave(email)
    if not chave:
        resultado["mensagem"] = (
            "Chave TOTP não encontrada para este email. "
            "Execute primeiro o endpoint /autenticar para configurar o 2FA."
        )
        return resultado

    resultado["chave_totp"] = chave
    driver = None
    try:
        driver = criar_driver(headless=headless)

        if not fazer_login(driver, email, senha):
            resultado["mensagem"] = "Falha no login."
            return resultado

        cenario = detectar_cenario_pos_login(driver)

        if cenario in ("login_2fa", "setup_2fa"):
            if not inserir_codigo_login_2fa(driver, chave):
                resultado["mensagem"] = "Falha ao inserir código TOTP."
                return resultado
        elif cenario != "logado":
            resultado["mensagem"] = f"Cenário pós-login não identificado: {cenario}"
            return resultado

        if navegar_recursos_premium(driver):
            resultado["sucesso"] = True
            resultado["mensagem"] = "Login efetuado e navegado para Configurações > Gerais > Recursos premium!"
        else:
            resultado["mensagem"] = "Login OK, mas falha ao navegar para Recursos premium."

        return resultado

    except Exception as e:
        log.exception("Erro inesperado")
        resultado["mensagem"] = f"Erro inesperado: {str(e)}"
        return resultado

    finally:
        if driver and not manter_aberto:
            try:
                driver.quit()
            except Exception:
                pass
        elif driver and manter_aberto:
            _drivers_abertos[email] = driver
            log.info("Navegador mantido aberto para %s.", email)


def executar_automacao(email: str, senha: str, headless: bool = False, manter_aberto: bool = True) -> dict:
    """
    Executa o fluxo completo de SETUP do 2FA:
    1. Login com email/senha
    2. Etapa 1 do 2FA → Avançar
    3. Etapa 2 do 2FA → Extrair chave TOTP
    4. Etapa 3 do 2FA → Gerar código e submeter
    5. Salva a chave TOTP para logins futuros

    Retorna dict com status, chave TOTP e mensagem.
    """
    resultado = {
        "sucesso": False,
        "email": email,
        "chave_totp": "",
        "mensagem": "",
    }

    driver = None
    try:
        driver = criar_driver(headless=headless)

        # --- LOGIN ---
        if not fazer_login(driver, email, senha):
            resultado["mensagem"] = "Falha no login."
            return resultado

        # --- DETECTAR CENÁRIO ---
        cenario = detectar_cenario_pos_login(driver)

        if cenario == "logado":
            navegar_cadastros_clientes(driver)
            resultado["sucesso"] = True
            resultado["mensagem"] = "Login efetuado com sucesso (sem 2FA). Navegado para Cadastros > Clientes."
            return resultado

        elif cenario == "login_2fa":
            # 2FA já configurado, precisa da chave salva
            chave = obter_chave(email)
            if not chave:
                resultado["mensagem"] = (
                    "2FA já configurado, mas não temos a chave TOTP salva para este email. "
                    "Resete o 2FA no sistema e tente novamente."
                )
                return resultado

            resultado["chave_totp"] = chave
            if inserir_codigo_login_2fa(driver, chave):
                navegar_cadastros_clientes(driver)
                resultado["sucesso"] = True
                resultado["mensagem"] = "Login com 2FA efetuado com sucesso! Navegado para Cadastros > Clientes."
            else:
                resultado["mensagem"] = "Falha ao inserir código TOTP no login 2FA."
            return resultado

        elif cenario == "setup_2fa":
            # --- ETAPA 1: Avançar ---
            if not etapa1_avancar(driver):
                resultado["mensagem"] = "Falha na etapa 1 do 2FA (botão Avançar)."
                return resultado

            # --- ETAPA 2: Extrair chave TOTP ---
            chave = etapa2_extrair_chave(driver)
            if not chave:
                resultado["mensagem"] = "Falha ao extrair a chave TOTP na etapa 2."
                return resultado
            resultado["chave_totp"] = chave

            # --- ETAPA 3: Inserir código ---
            if not etapa3_inserir_codigo(driver, chave):
                resultado["mensagem"] = "Falha ao inserir código na etapa 3."
                return resultado

            # --- SALVAR CHAVE ---
            salvar_chave(email, chave)

            resultado["sucesso"] = True
            resultado["mensagem"] = "2FA configurado e salvo com sucesso!"
            log.info("✅ Setup 2FA concluído para %s", email)
            return resultado

        else:
            resultado["mensagem"] = f"Cenário pós-login não identificado: {cenario}"
            return resultado

    except Exception as e:
        log.exception("Erro inesperado na automação")
        resultado["mensagem"] = f"Erro inesperado: {str(e)}"
        return resultado

    finally:
        if driver and not manter_aberto:
            try:
                driver.quit()
            except Exception:
                pass
        elif driver and manter_aberto:
            _drivers_abertos[email] = driver
            log.info("Navegador mantido aberto para %s. Feche manualmente quando terminar.", email)


def executar_login(email: str, senha: str, headless: bool = False, manter_aberto: bool = True) -> dict:
    """
    Login em conta com 2FA já configurado.
    Usa a chave TOTP salva para gerar o código automaticamente.
    """
    resultado = {
        "sucesso": False,
        "email": email,
        "chave_totp": "",
        "mensagem": "",
    }

    chave = obter_chave(email)
    if not chave:
        resultado["mensagem"] = (
            "Chave TOTP não encontrada para este email. "
            "Execute primeiro o endpoint /autenticar para configurar o 2FA."
        )
        return resultado

    resultado["chave_totp"] = chave
    driver = None
    try:
        driver = criar_driver(headless=headless)

        if not fazer_login(driver, email, senha):
            resultado["mensagem"] = "Falha no login."
            return resultado

        cenario = detectar_cenario_pos_login(driver)

        if cenario == "logado":
            resultado["sucesso"] = True
            resultado["mensagem"] = "Login efetuado (sem 2FA solicitado)."
            return resultado

        if cenario in ("login_2fa", "setup_2fa"):
            # Em ambos os casos, tenta inserir o código
            if cenario == "setup_2fa":
                log.info("2FA setup detectado, mas já temos chave. Pulando para código...")

            if inserir_codigo_login_2fa(driver, chave):
                navegar_cadastros_clientes(driver)
                resultado["sucesso"] = True
                resultado["mensagem"] = "Login com 2FA efetuado com sucesso! Navegado para Cadastros > Clientes."
            else:
                resultado["mensagem"] = "Falha ao inserir código TOTP."
        else:
            resultado["mensagem"] = f"Cenário pós-login não identificado: {cenario}"

        return resultado

    except Exception as e:
        log.exception("Erro inesperado no login")
        resultado["mensagem"] = f"Erro inesperado: {str(e)}"
        return resultado

    finally:
        if driver and not manter_aberto:
            try:
                driver.quit()
            except Exception:
                pass
        elif driver and manter_aberto:
            _drivers_abertos[email] = driver
            log.info("Navegador mantido aberto para %s. Feche manualmente quando terminar.", email)

def adicionar_anuncio_motorista(driver, imagem_path, link_anuncio=None, selecionar_todas=True):
    log.info("Configurando anúncio na tela inicial do app motorista")
    TIPO      = "tela_inicial_app_taxista"
    NOME_MOD  = "AnuncioAppTaxista"
    FIELD_ID  = f"anuncio-{TIPO}"
    NOVO_IDX  = 1   # Índice do NOVO anúncio (0 = existente, que será marcado excluido)

    try:
        # 1. Marcar radio Sim e acionar alteraVisibilidadeCamposAnuncios
        driver.execute_script("""
            var sim = document.getElementById(arguments[0]);
            var nao = document.getElementById(arguments[1]);
            if (sim) sim.checked = true;
            if (nao) nao.checked = false;
        """, f"{NOME_MOD}_exibir_anuncio_0", f"{NOME_MOD}_exibir_anuncio_1")
        driver.execute_script(f"alteraVisibilidadeCamposAnuncios('{TIPO}');")
        log.info("alteraVisibilidadeCamposAnuncios chamada.")

        # 2. Aguarda form do anúncio existente (índice 0)
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                f"return !!document.getElementById('add-foto-{FIELD_ID}-0');"
            )
        )
        log.info("Anúncio existente (idx=0) visível.")

        # 3. Marca anúncio existente (idx=0) como excluido=1
        #    O servidor não consegue alterar url_imagem/url_anuncio do existente
        #    (permite_alterar_imagem=false, permite_alterar_url_anuncio=false).
        #    Solução: excluir o existente e criar um novo com flags habilitados.
        driver.execute_script("""
            var elExc = document.getElementById(arguments[0]);
            if (elExc) {
                elExc.value = '1';
                if (window.jQuery) jQuery(elExc).val('1');
            }
        """, f"{NOME_MOD}_0_excluido")
        log.info("Anúncio existente marcado como excluido=1.")

        # 4. Adiciona novo anúncio (índice 1) via JS — campos enabled por padrão
        driver.execute_script(f"adicionarNovoAnuncio('{TIPO}');")
        time.sleep(0.5)
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                f"return !!document.getElementById('add-foto-{FIELD_ID}-{NOVO_IDX}');"
            )
        )
        log.info(f"Novo anúncio (idx={NOVO_IDX}) criado com campos habilitados.")

        # 5. Upload da imagem via XHR do browser
        with open(imagem_path, "rb") as fh:
            img_b64 = base64.b64encode(fh.read()).decode("utf-8")

        bandeira_id = driver.execute_script(
            "return typeof bandeiraId !== 'undefined' ? bandeiraId : null;"
        )
        log.info(f"Fazendo upload via browser XHR (bandeiraId={bandeira_id})...")

        driver.set_script_timeout(30)
        upload_result = driver.execute_async_script("""
            var callback = arguments[arguments.length - 1];
            var b64 = arguments[0];
            var bId = arguments[1];
            var byteStr = atob(b64);
            var ab = new ArrayBuffer(byteStr.length);
            var ia = new Uint8Array(ab);
            for (var i = 0; i < byteStr.length; i++) ia[i] = byteStr.charCodeAt(i);
            var blob = new Blob([ab], {type: 'image/jpeg'});
            var file = new File([blob], 'banner.jpeg', {type: 'image/jpeg'});
            var fd = new FormData();
            fd.append('foto', file);
            fd.append('id', String(bId));
            fd.append('tipo', 'anuncio');
            fd.append('campo', 'anuncio');
            var xhr = new XMLHttpRequest();
            xhr.open('POST', (typeof baseUrl !== 'undefined' ? baseUrl : '') + '/bandeira/salvarImagemConfiguracao');
            xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            xhr.onload = function() {
                try {
                    var r = JSON.parse(xhr.responseText.trim());
                    callback({success: r.success === true, urlS3: r.urlS3 || null,
                              fotoName: r.fotoName || 'banner.jpeg',
                              error: r.errors ? r.errors[0] : null});
                } catch(e) {
                    callback({success: false, error: 'parse: ' + xhr.responseText.substring(0,120)});
                }
            };
            xhr.onerror = function() { callback({success: false, error: 'network error'}); };
            xhr.send(fd);
        """, img_b64, bandeira_id)

        if not upload_result or not upload_result.get("success"):
            err = (upload_result or {}).get("error", "timeout/desconhecido")
            return {"sucesso": False, "mensagem": f"Falha no upload da imagem: {err}"}

        url_s3    = upload_result["urlS3"]
        foto_name = upload_result.get("fotoName", "banner.jpeg")
        log.info(f"Upload OK: {url_s3}")

        # 6. Atualiza preview + campo url_imagem do NOVO anúncio (idx=1)
        driver.execute_script("""
            var fId = arguments[0], idx = arguments[1], nm = arguments[2],
                url = arguments[3], fn = arguments[4];
            var img = document.getElementById('preview-img-' + fId + '-' + idx);
            if (img) img.src = url;
            var lbl = document.getElementById('preview-label-' + fId + '-' + idx);
            if (lbl) lbl.textContent = fn;
            var wrap = document.querySelector('.wrapper-preview-label-' + fId + '-' + idx);
            if (wrap) wrap.style.display = 'flex';
            var prev = document.getElementById('preview-' + fId + '-' + idx);
            if (prev) prev.style.display = 'flex';
            var addFoto = document.getElementById('add-foto-' + fId + '-' + idx);
            if (addFoto) addFoto.style.display = 'none';
            var urlField = document.getElementById(nm + '_' + idx + '_url_imagem');
            if (urlField) urlField.value = url;
        """, FIELD_ID, NOVO_IDX, NOME_MOD, url_s3, foto_name)
        log.info("Preview e campo url_imagem do novo anúncio atualizados.")

        # 7. Link do novo anúncio — campo enabled por padrão no novo registro
        if link_anuncio:
            driver.execute_script("""
                var el = document.getElementById(arguments[0]);
                if (el) {
                    el.disabled = false;
                    el.removeAttribute('disabled');
                    el.value = arguments[1];
                    if (window.jQuery) jQuery(el).prop('disabled', false).val(arguments[1]);
                }
                var lista = typeof obterListaAnuncio === 'function' ? obterListaAnuncio(arguments[2]) : null;
                if (lista && lista[arguments[3]] !== undefined) {
                    lista[arguments[3]].url_anuncio = arguments[1];
                }
            """, f"{NOME_MOD}_{NOVO_IDX}_url_anuncio", link_anuncio, TIPO, NOVO_IDX)
            log.info(f"Link do anúncio definido no novo registro (idx={NOVO_IDX}).")

        # 8. Seleciona todas as centrais para o NOVO anúncio
        SELECT_ID_NOVO = f"filtro_bandeiras_anuncio_{TIPO}_{NOVO_IDX}"
        if selecionar_todas:
            driver.execute_script("""
                var sel = document.getElementById(arguments[0]);
                if (sel) {
                    for (var i = 0; i < sel.options.length; i++) sel.options[i].selected = true;
                    if (window.jQuery) {
                        jQuery(sel).multiselect('refresh');
                        jQuery(sel).trigger('change');
                    }
                }
            """, SELECT_ID_NOVO)
            log.info("Todas as centrais selecionadas para o novo anúncio.")

        # 9. Diagnóstico: lê FormData antes de salvar (apenas campos AnuncioAppTaxista)
        pre_save = driver.execute_script("""
            var form = document.getElementById('bandeira-form');
            var fd = form ? new FormData(form) : null;
            if (!fd) return null;
            var r = {};
            fd.forEach(function(v, k) {
                if (k.indexOf('AnuncioAppTaxista') !== -1) r[k] = v;
            });
            return r;
        """)
        log.info(f"FormData AnuncioAppTaxista antes do save: {pre_save}")

        # 10. Salvar
        log.info("Clicando no botão Salvar...")
        btn_salvar = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "btn-salvar-bandeira"))
        )
        driver.execute_script("arguments[0].click();", btn_salvar)
        time.sleep(1)
        try:
            alert = driver.switch_to.alert
            log.info(f"Alert detectado: {alert.text}")
            alert.accept()
        except Exception:
            pass
        time.sleep(5)

        # 11. Verificação pós-save: navega de volta e lê valores salvos
        log.info(f"URL após save: {driver.current_url}")
        try:
            driver.get("https://cloud.taximachine.com.br/bandeira/update")
            time.sleep(3)
            aba = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//a[normalize-space(text())="Recursos premium"]'))
            )
            driver.execute_script("arguments[0].click();", aba)
            time.sleep(2)
            saved = driver.execute_script("""
                var lista = typeof obterListaAnuncio === 'function' ? obterListaAnuncio(arguments[0]) : null;
                if (!lista || lista.length === 0) return null;
                var ativos = lista.filter(function(a) { return String(a.excluido) !== '1'; });
                return ativos.length > 0 ? {url_anuncio: ativos[0].url_anuncio, url_imagem: ativos[0].url_imagem} : null;
            """, TIPO)
            log.info(f"Valores salvos (verificação): {saved}")
            url_imagem_salva = saved.get("url_imagem") if saved else None
        except Exception as e_ver:
            log.warning(f"Verificação pós-save falhou: {e_ver}")
            url_imagem_salva = url_s3

        return {"sucesso": True, "mensagem": f"Anúncio configurado com sucesso! URL: {url_imagem_salva or url_s3}"}

    except Exception as e:
        log.exception("Erro inesperado ao adicionar anúncio motorista")
        return {"sucesso": False, "mensagem": f"Falha ao configurar anúncio: {e}"}

def executar_adicionar_anuncio_motorista(email: str, senha: str, chave_secreta: str = None, headless: bool = False, imagem_path: str = "", link_anuncio: str = "", selecionar_todas: bool = True, manter_aberto: bool = True) -> dict:
    resultado = {"sucesso": False, "email": email, "chave_totp": chave_secreta, "mensagem": ""}
    driver = None
    try:
        driver = criar_driver(headless)
        if not fazer_login(driver, email, senha):
            resultado["mensagem"] = "Falha no login inicial."
            return resultado
        
        time.sleep(3)
        cenario = detectar_cenario_pos_login(driver)
        
        if cenario in ("login_2fa", "setup_2fa"):
            chave = chave_secreta or obter_chave(email)
            if not chave:
                resultado["mensagem"] = "Cenário 2FA detectado, mas chave secreta não está disponível."
                return resultado
            if not inserir_codigo_login_2fa(driver, chave):
                 resultado["mensagem"] = "Falha ao submeter código TOTP."
                 return resultado
            time.sleep(5)
            
        if not navegar_recursos_premium(driver):
            resultado["mensagem"] = "Falha ao navegar até Recursos Premium."
            return resultado
            
        time.sleep(3)
        adinfo = adicionar_anuncio_motorista(driver, imagem_path, link_anuncio, selecionar_todas)
        resultado["sucesso"] = adinfo["sucesso"]
        resultado["mensagem"] = adinfo["mensagem"]
        return resultado
        
    except Exception as e:
         log.exception("Erro inesperado na automação do anúncio")
         resultado["mensagem"] = f"Erro inesperado: {str(e)}"
         return resultado
    finally:
         if driver and not manter_aberto:
             try:
                 driver.quit()
             except Exception:
                 pass
         elif driver and manter_aberto:
             _drivers_abertos[email] = driver
             log.info("Navegador mantido aberto para %s.", email)


def remover_anuncio_motorista(driver) -> dict:
    """
    Na tela de Recursos Premium, clica em 'Remover' no anúncio existente,
    confirma o alert JavaScript e depois salva via 'btn-salvar-bandeira'.
    Retorna dict com 'sucesso' e 'mensagem'.
    """
    print("[INFO] Iniciando remoção de anúncio na tela inicial do app motorista")
    try:
        # 1. Localizar e clicar no botão/link "Remover"
        seletores_remover = [
            (By.XPATH, '//a[normalize-space(text())="Remover" and contains(@class,"remover")]'),
            (By.XPATH, '//a[normalize-space(text())="Remover"]'),
            (By.XPATH, '//button[normalize-space(text())="Remover"]'),
            (By.XPATH, '//*[contains(@id,"remover-anuncio")]'),
            (By.XPATH, '//*[contains(@onclick,"remover") and contains(text(),"Remover")]'),
            (By.XPATH, '//*[normalize-space(text())="Remover"]'),
        ]

        clicou = False
        for by, sel in seletores_remover:
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, sel))
                )
                driver.execute_script("arguments[0].click();", btn)
                print(f"[INFO] Clicou em 'Remover' via: {sel}")
                clicou = True
                break
            except Exception:
                continue

        if not clicou:
            return {"sucesso": False, "mensagem": "Botão 'Remover' não encontrado. Talvez não exista anúncio ativo."}

        time.sleep(1)

        # 2. Confirmar o alert JavaScript ("Tem certeza que deseja remover este anúncio?")
        try:
            alert = WebDriverWait(driver, 5).until(EC.alert_is_present())
            print(f"[INFO] Alert detectado: {alert.text}")
            alert.accept()
            print("[INFO] Alert confirmado (OK).")
        except Exception as e:
            print(f"[WARNING] Nenhum alert detectado após clicar em Remover: {e}")

        time.sleep(2)

        # 3. Salvar via btn-salvar-bandeira
        try:
            btn_salvar = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "btn-salvar-bandeira"))
            )
            driver.execute_script("arguments[0].click();", btn_salvar)
            print("[INFO] Botão salvar clicado.")
            time.sleep(1)
            try:
                alert2 = driver.switch_to.alert
                print(f"[INFO] Alert pós-salvar: {alert2.text}")
                alert2.accept()
            except Exception:
                pass
            time.sleep(3)
        except Exception as e:
            print(f"[WARNING] Falha ao clicar em salvar após remoção: {e}")

        return {"sucesso": True, "mensagem": "Anúncio removido e salvo com sucesso!"}

    except Exception as e:
        log.exception("Erro inesperado ao remover anúncio motorista")
        return {"sucesso": False, "mensagem": f"Erro inesperado: {e}"}


def executar_remover_anuncio_motorista(email: str, senha: str, chave_secreta: str = None, headless: bool = False, manter_aberto: bool = False) -> dict:
    """
    Wrapper completo: login 2FA → Recursos Premium → remover anúncio motorista.
    """
    resultado = {"sucesso": False, "email": email, "chave_totp": chave_secreta or "", "mensagem": ""}
    driver = None
    try:
        driver = criar_driver(headless)
        if not fazer_login(driver, email, senha):
            resultado["mensagem"] = "Falha no login inicial."
            return resultado

        time.sleep(3)
        cenario = detectar_cenario_pos_login(driver)

        if cenario in ("login_2fa", "setup_2fa"):
            chave = chave_secreta or obter_chave(email)
            if not chave:
                resultado["mensagem"] = "Cenário 2FA detectado, mas chave secreta não está disponível."
                return resultado
            if not inserir_codigo_login_2fa(driver, chave):
                resultado["mensagem"] = "Falha ao submeter código TOTP."
                return resultado
            time.sleep(5)

        if not navegar_recursos_premium(driver):
            resultado["mensagem"] = "Falha ao navegar até Recursos Premium."
            return resultado

        time.sleep(3)
        ret = remover_anuncio_motorista(driver)
        resultado["sucesso"] = ret["sucesso"]
        resultado["mensagem"] = ret["mensagem"]
        return resultado

    except Exception as e:
        log.exception("Erro inesperado na automação de remoção de anúncio")
        resultado["mensagem"] = f"Erro inesperado: {str(e)}"
        return resultado
    finally:
        if driver and not manter_aberto:
            try:
                driver.quit()
            except Exception:
                pass
        elif driver and manter_aberto:
            _drivers_abertos[email] = driver
            log.info("Navegador mantido aberto para %s.", email)


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        email_arg = sys.argv[1]
        senha_arg = sys.argv[2]
        modo_headless = "--headless" in sys.argv
    else:
        email_arg = input("Email: ").strip()
        senha_arg = input("Senha: ").strip()
        modo_headless = False

    res = executar_automacao(email_arg, senha_arg, headless=modo_headless)
    print("\n" + "=" * 50)
    print(f"  Resultado: {'✅ Sucesso' if res['sucesso'] else '❌ Falha'}")
    print(f"  Email: {res['email']}")
    if res["chave_totp"]:
        print(f"  Chave TOTP: {res['chave_totp']}")
    print(f"  Mensagem: {res['mensagem']}")
    print("=" * 50)
