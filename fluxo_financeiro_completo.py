#!/usr/bin/env python3
"""
Fluxo Financeiro Completo - Versão Final

Credenciais: FIN_EMAIL, FIN_SENHA (ou padrão no código).
Webhook: FIN_WEBHOOK_URL (ou URL fixa abaixo).

Uso:
  python3 fluxo_financeiro_completo.py
  python3 fluxo_financeiro_completo.py --headless --no-wait
"""
import time
import sys
import os
import re
import json
import argparse
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_2fa import (
    criar_driver,
    fazer_login,
    detectar_cenario_pos_login,
    inserir_codigo_login_2fa,
    obter_chave,
    _drivers_abertos,
)

DEFAULT_EMAIL = "Automacaofinanceiro@alfenas.com"
DEFAULT_SENHA = "Reinaldo2026@"
DEFAULT_WEBHOOK = (
    "https://api-webhook.estruturadeapi.com/webhook/"
    "2b5540f8-18d8-42d2-8c17-3c48d31b6ec1"
)
DADOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados_financeiro_completo.json")

# Painel onde aparecem período (Mês passado) e Ganhos gerais — após 2FA o destino varia.
URLS_PAINEL_GANHOS = [
    "https://cloud.taximachine.com.br/",
    "https://cloud.taximachine.com.br/site/index",
    "https://cloud.taximachine.com.br/bandeira/index",
]


def _html_tem_widgets_ganhos(driver) -> bool:
    s = (driver.page_source or "").lower()
    return (
        "periodo-select-button" in s
        or "ganho-geral-numero" in s
        or "periodo-mes_passado" in s
    )


def _aguardar_widgets_painel(driver, segundos: float = 35) -> bool:
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(driver, segundos).until(lambda d: _html_tem_widgets_ganhos(d))
        return True
    except Exception:
        return False


def tentar_menu_painel_ou_inicio(driver) -> bool:
    """Clica em link do menu que leva ao painel (SPA demora a injetar IDs)."""
    from selenium.webdriver.common.by import By

    xpaths = [
        "//a[contains(@href,'site/index')]",
        "//a[contains(@href,'/site/index')]",
        "//nav//a[contains(normalize-space(.),'Painel')]",
        "//a[contains(.,'Painel') and contains(@href,'http')]",
        "//header//a[contains(@href,'index')]",
    ]
    for xp in xpaths:
        try:
            for a in driver.find_elements(By.XPATH, xp):
                try:
                    if not a.is_displayed():
                        continue
                    href = (a.get_attribute("href") or "").lower()
                    if "logout" in href or "sair" in href:
                        continue
                    driver.execute_script("arguments[0].click();", a)
                    time.sleep(5)
                    fechar_avisos_comuns(driver)
                    if _aguardar_widgets_painel(driver, 25):
                        return True
                except Exception:
                    pass
        except Exception:
            pass
    return False


def fechar_avisos_comuns(driver) -> None:
    """Fecha modais que bloqueiam o painel (ex.: ciência, atenção)."""
    from selenium.webdriver.common.by import By

    xpaths = [
        "//button[contains(., 'Estou ciente')]",
        "//button[contains(., 'estou ciente')]",
        "//a[contains(., 'Fechar') and contains(@class, 'close')]",
        "//*[contains(@class,'modal')]//button[contains(., 'OK')]",
    ]
    for _ in range(3):
        clicou = False
        for xp in xpaths:
            try:
                for el in driver.find_elements(By.XPATH, xp):
                    try:
                        if el.is_displayed():
                            driver.execute_script("arguments[0].click();", el)
                            clicou = True
                            time.sleep(0.4)
                    except Exception:
                        pass
            except Exception:
                pass
        if not clicou:
            break


def navegar_painel_ganhos_gerais(driver) -> bool:
    """
    Garante página com widgets de período / Ganhos gerais (IDs ou HTML).
    Docker/SPA: espera na URL atual primeiro, depois tenta URLs e menu.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    fechar_avisos_comuns(driver)
    try:
        print(f"   📍 URL após login: {driver.current_url}")
    except Exception:
        pass

    # 1) Própria página pós-2FA (às vezes já é o painel; o React injeta os IDs depois)
    try:
        WebDriverWait(driver, 8).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass
    time.sleep(4)
    fechar_avisos_comuns(driver)
    if _aguardar_widgets_painel(driver, 28):
        try:
            WebDriverWait(driver, 12).until(
                EC.any_of(
                    EC.presence_of_element_located((By.ID, "periodo-select-button")),
                    EC.presence_of_element_located((By.ID, "ganho-geral-numero")),
                )
            )
        except Exception:
            pass
        print("   ✅ Painel (widgets ganhos) na página atual")
        return True

    # 2) Menu “Painel” / link site/index
    if tentar_menu_painel_ou_inicio(driver):
        print("   ✅ Painel via menu/link")
        return True

    # 3) URLs candidatas + espera longa (SPA)
    for url in URLS_PAINEL_GANHOS:
        print(f"   🏠 Abrindo painel: {url}")
        driver.get(url)
        time.sleep(5)
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass
        fechar_avisos_comuns(driver)
        time.sleep(3)
        if _aguardar_widgets_painel(driver, 35):
            try:
                WebDriverWait(driver, 15).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.ID, "periodo-select-button")),
                        EC.presence_of_element_located((By.ID, "ganho-geral-numero")),
                    )
                )
            except Exception:
                pass
            print("   ✅ Painel encontrado após navegar")
            return True
        if tentar_menu_painel_ou_inicio(driver):
            print("   ✅ Painel após menu (pós-GET)")
            return True

    print("   ⚠️ Não achei widgets de ganhos/período (HTML nem IDs)")
    return False


def enviar_json_webhook(url: str, payload: dict, timeout: int = 90) -> tuple:
    """POST JSON no webhook. Retorna (status_code, corpo_texto ou mensagem erro)."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "AUTO_FINANCEIRO-fluxo-financeiro/1.0",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, err_body or str(e)
    except Exception as e:
        return -1, str(e)


def fazer_login_completo(driver, email: str, senha: str) -> bool:
    """Faz login completo com 2FA."""
    print("🔐 Fazendo login...")
    driver.get("https://cloud.taximachine.com.br/")
    time.sleep(2)

    if not fazer_login(driver, email, senha):
        return False

    print("✅ Login OK")
    time.sleep(3)

    cenario = detectar_cenario_pos_login(driver)
    chave = obter_chave(email)

    if cenario == "setup_2fa":
        from auto_2fa import etapa1_avancar, etapa2_extrair_chave, salvar_chave, etapa3_inserir_codigo

        etapa1_avancar(driver)
        time.sleep(2)
        chave = etapa2_extrair_chave(driver)
        if chave:
            etapa3_inserir_codigo(driver, chave)
            salvar_chave(email, chave)
    elif cenario == "login_2fa":
        inserir_codigo_login_2fa(driver, chave)

    # Redirect + SPA pós-2FA (Docker costuma precisar de mais tempo que local)
    time.sleep(8)
    fechar_avisos_comuns(driver)
    return True


def aplicar_filtro_mes_passado(driver):
    """Aplica filtro Mês passado."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    print("\n📅 Aplicando filtro 'Mês passado'...")

    try:
        wait = WebDriverWait(driver, 25)
        botao = wait.until(EC.element_to_be_clickable((By.ID, "periodo-select-button")))
        driver.execute_script("arguments[0].click();", botao)
        time.sleep(2)

        opcao = wait.until(EC.element_to_be_clickable((By.ID, "periodo-mes_passado")))
        driver.execute_script("arguments[0].click();", opcao)
        print("   ✅ 'Mês passado' selecionado")
        time.sleep(8)
        return True
    except Exception as e:
        print(f"   ⚠️ Erro: {e}")
        return False


def _parse_decimal_br(s: str) -> float:
    """Interpreta número como no site: 1.234,56 ou 5,00 ou 5.00."""
    s = str(s).strip().replace("R$", "").strip()
    if not s:
        raise ValueError("vazio")
    last_c = s.rfind(",")
    last_d = s.rfind(".")
    if last_c != -1 and last_c > last_d:
        return float(s.replace(".", "").replace(",", "."))
    return float(s.replace(",", "."))


def _format_pct_br(val: str) -> str:
    s = (val or "").strip().replace(",", ".")
    if not s:
        return "0%"
    try:
        n = float(s)
        if abs(n - round(n)) < 1e-9:
            return f"{int(round(n))}%"
        t = f"{n:.2f}".rstrip("0").rstrip(".")
        return f"{t}%"
    except ValueError:
        return f"{val}%"


def _format_real_br(val: str) -> str:
    if val is None or not str(val).strip():
        return "R$ 0,00"
    s = str(val).strip()
    try:
        n = _parse_decimal_br(s)
    except ValueError:
        return f"R$ {s}"
    neg = n < 0
    n = abs(n)
    inteiro = int(n)
    cents = int(round((n - inteiro) * 100 + 1e-9))
    if cents >= 100:
        inteiro += cents // 100
        cents = cents % 100
    part = f"{inteiro:,}".replace(",", ".")
    out = f"R$ {part},{cents:02d}"
    return f"- {out}" if neg else out


def extrair_ganhos_gerais(driver):
    """Extrai Ganhos gerais."""
    print("\n💰 Extraindo 'Ganhos gerais'...")
    
    from selenium.webdriver.common.by import By
    
    try:
        time.sleep(3)
        
        try:
            elemento = driver.find_element(By.ID, "ganho-geral-numero")
            texto = elemento.text.strip()
            if texto:
                valor = f"R$ {texto}"
                print(f"   ✅ Ganhos gerais: {valor}")
                return valor
        except:
            pass
        
        page = driver.page_source
        idx = page.lower().find('ganhos gerais')
        if idx >= 0:
            trecho = page[idx:idx+500]
            match = re.search(r'<p[^>]*class="number count"[^>]*>([\d\.]+,\d{2})', trecho)
            if match:
                valor = f"R$ {match.group(1)}"
                print(f"   ✅ Ganhos gerais: {valor}")
                return valor
        
        return None
    except Exception as e:
        print(f"   ❌ Erro: {e}")
        return None


def extrair_taxas(driver):
    """
    Extrai taxas dos cartões da listagem (button.short.btn-tax).
    Os formulários ocultos repetem os mesmos name=\"TaxaBandeira[...]\" — find_element só pegava a 1ª taxa.
    """
    print("\n💳 Extraindo taxas...")

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    taxas: dict = {}

    def _parse_taxa_visivel(texto: str) -> tuple:
        """Ex.: '15% - R$5,00', 'R$0,20', só '15%' -> (pct|None, fixo|None)."""
        if not texto:
            return None, None
        t = texto.replace("\n", " ").strip()
        m = re.search(
            r"(\d+(?:[.,]\d+)?)\s*%\s*[-–]\s*R\$\s*([\d.,]+)",
            t,
            re.I,
        )
        if m:
            return m.group(1).replace(",", "."), m.group(2)
        m2 = re.search(r"R\$\s*([\d.,]+)", t, re.I)
        if m2:
            return None, m2.group(1)
        # Cartão só com percentual (ex.: "15%"), sem parte em R$
        m3 = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
        if m3:
            return m3.group(1).replace(",", "."), None
        return None, None

    titulo_para_chave = {
        "taxa da central": "taxa_central",
        "taxa app": "taxa_app",
        "taxa seguro app": "taxa_seguro_app",
    }

    try:
        print("   🔗 Home antes de créditos (fixar sessão/cookies)…")
        driver.get("https://cloud.taximachine.com.br/")
        time.sleep(5)
        fechar_avisos_comuns(driver)
        driver.get("https://cloud.taximachine.com.br/bandeira/creditos")
        time.sleep(6)
        try:
            WebDriverWait(driver, 45).until(
                EC.any_of(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "button.short.btn-tax")),
                    EC.presence_of_element_located(
                        (By.NAME, "TaxaBandeira[valor_taxa_central_fx1]")
                    ),
                )
            )
        except Exception:
            time.sleep(6)

        src_l = (driver.page_source or "").lower()
        url_l = (driver.current_url or "").lower()
        if (
            "sessão foi encerrada" in src_l
            or "sessao foi encerrada" in src_l
            or "sua sessão foi encerrada" in src_l
            or "/login" in url_l
            or url_l.endswith("taximachine.com.br/login")
        ):
            print("   ⚠️ Sessão inválida ou tela de login em créditos")

        botoes = driver.find_elements(By.CSS_SELECTOR, "button.short.btn-tax")

        for btn in botoes:
            try:
                titulo = btn.find_element(By.CSS_SELECTOR, ".short_title").text.strip()
            except Exception:
                continue
            chave = titulo_para_chave.get(titulo.lower())
            if not chave:
                continue

            taxa_txt = ""
            cats_txt = ""
            pay_txt = ""
            try:
                taxa_txt = btn.find_element(By.CSS_SELECTOR, ".taxa-content").text.strip()
            except Exception:
                pass
            try:
                cats_txt = btn.find_element(By.CSS_SELECTOR, ".categorias-content").text.strip()
            except Exception:
                pass
            try:
                pay_txt = btn.find_element(By.CSS_SELECTOR, ".tipos-pagamento-content").text.strip()
            except Exception:
                pass

            pct_raw, fixo_raw = _parse_taxa_visivel(taxa_txt)
            categorias = (
                [c.strip() for c in re.split(r",\s*", cats_txt) if c.strip()]
                if cats_txt
                else []
            )

            if chave == "taxa_seguro_app":
                bloco = {"valor_fixo": _format_real_br(fixo_raw or "")}
            else:
                # Central / App: pode ser só %, só R$, ou ambos — nunca inventar R$ 0,00
                bloco = {}
                if pct_raw:
                    bloco["porcentagem"] = _format_pct_br(pct_raw)
                if fixo_raw:
                    bloco["valor_fixo"] = _format_real_br(fixo_raw)
                if not bloco:
                    bloco["valor_fixo"] = _format_real_br("")
            if categorias:
                bloco["categorias"] = categorias
            if pay_txt:
                bloco["tipos_pagamento"] = pay_txt
            taxas[chave] = bloco

            print(f"   📌 {titulo}:")
            if chave == "taxa_seguro_app":
                print(f"      • {bloco['valor_fixo']}")
            elif "porcentagem" in bloco and "valor_fixo" in bloco:
                print(f"      • {bloco['porcentagem']} - {bloco['valor_fixo']}")
            elif "porcentagem" in bloco:
                print(f"      • {bloco['porcentagem']}")
            else:
                print(f"      • {bloco.get('valor_fixo', '-')}")
            if categorias:
                amostra = categorias[:10]
                suf = "…" if len(categorias) > 10 else ""
                print(f"      • Categorias ({len(categorias)}): {', '.join(amostra)}{suf}")
            if pay_txt:
                print(f"      • Pagamento: {pay_txt}")

        # Fallback mínimo: só taxa da central via 1º formulário (layout antigo sem cartões)
        if "taxa_central" not in taxas:
            print("   ⚠️ Cartões não encontrados; tentando inputs da 1ª taxa…")
            try:
                pct_raw = driver.find_element(
                    By.NAME, "TaxaBandeira[valor_taxa_central_fx1]"
                ).get_attribute("value")
                fixo_raw = driver.find_element(
                    By.NAME, "TaxaBandeira[valor_taxa_central_fx3]"
                ).get_attribute("value")
                taxas["taxa_central"] = {
                    "porcentagem": _format_pct_br(pct_raw or ""),
                    "valor_fixo": _format_real_br(fixo_raw or ""),
                }
                print(
                    f"   📌 Taxa da central (fallback): "
                    f"{taxas['taxa_central']['porcentagem']} - {taxas['taxa_central']['valor_fixo']}"
                )
            except Exception as e:
                print(f"      ⚠️ {e}")

        return taxas

    except Exception as e:
        print(f"   ❌ Erro: {e}")
        import traceback

        traceback.print_exc()
        return {}


def executar_fluxo_financeiro_completo(
    email: str,
    senha: str,
    *,
    headless: bool = False,
    no_wait: bool = False,
    enviar_webhook: bool = True,
    webhook_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Executa login, ganhos (mês passado), taxas, grava JSON e opcionalmente webhook.
    Se no_wait=False, mantém o Chrome aberto em loop (não retorna em sucesso).
    Se no_wait=True, encerra o driver e devolve dict com sucesso, payload e dados.
    """
    wh_url = webhook_url or os.environ.get("FIN_WEBHOOK_URL", DEFAULT_WEBHOOK)

    print("=" * 70)
    print("FLUXO FINANCEIRO COMPLETO")
    print("=" * 70)

    driver = criar_driver(headless=headless)
    dados: Dict[str, Any] = {}

    try:
        if not fazer_login_completo(driver, email, senha):
            print("❌ Falha no login")
            try:
                driver.quit()
            except Exception:
                pass
            return {
                "sucesso": False,
                "mensagem": "Falha no login",
                "email": email,
                "chave_totp": "",
            }

        fechar_avisos_comuns(driver)

        avisos: Dict[str, Any] = {}
        try:
            avisos["url_pos_login"] = driver.current_url
        except Exception:
            avisos["url_pos_login"] = ""

        print("\n" + "=" * 70)
        print("ETAPA 1: Ganhos gerais (Mês passado)")
        print("=" * 70)

        if navegar_painel_ganhos_gerais(driver):
            avisos["painel_ganhos"] = "ok"
        else:
            avisos["painel_ganhos"] = "elementos_nao_encontrados"

        filtro_ok = aplicar_filtro_mes_passado(driver)
        avisos["filtro_mes_passado"] = "ok" if filtro_ok else "falhou"
        if filtro_ok:
            ganho = extrair_ganhos_gerais(driver)
            if ganho:
                dados["ganho_geral_mes_passado"] = ganho
                avisos["ganhos_gerais"] = "ok"
            else:
                avisos["ganhos_gerais"] = "nao_extraido"
        else:
            # Ainda tenta ler o número exibido (período atual) para não voltar vazio em painel lento
            ganho = extrair_ganhos_gerais(driver)
            if ganho:
                dados["ganho_geral_mes_passado"] = ganho
                dados["ganho_geral_observacao"] = (
                    "Valor lido sem confirmar filtro 'Mês passado' (filtro falhou ou período já era mês passado)."
                )
                avisos["ganhos_gerais"] = "lido_sem_filtro_confirmado"
            else:
                avisos["ganhos_gerais"] = "nao_extraido"

        print("\n" + "=" * 70)
        print("ETAPA 2: Taxas (Controle de créditos)")
        print("=" * 70)

        taxas = extrair_taxas(driver)
        dados.update(taxas)
        avisos["taxas_chaves"] = list(taxas.keys()) if taxas else []
        avisos["taxas_ok"] = bool(taxas)

        dados["data_extracao"] = datetime.now().isoformat()
        dados["email_conta_taximachine"] = email
        dados["avisos_extracao"] = avisos

        payload_webhook = {
            "evento": "taximachine_financeiro_completo",
            "extraido_em": dados["data_extracao"],
            "email_conta": email,
            "dados_extraidos": dados,
        }

        with open(DADOS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload_webhook, f, indent=2, ensure_ascii=False)

        print("\n" + "=" * 70)
        print("✅ JSON COMPLETO (payload webhook)")
        print("=" * 70)
        print(json.dumps(payload_webhook, indent=2, ensure_ascii=False))
        print("=" * 70)
        print(f"\n📁 Arquivo: {DADOS_FILE}")
        print("=" * 70)

        webhook_status = None
        webhook_corpo = None
        if enviar_webhook:
            print("\n📤 Enviando POST para webhook…")
            webhook_status, webhook_corpo = enviar_json_webhook(wh_url, payload_webhook)
            print(f"   Status HTTP: {webhook_status}")
            if webhook_corpo:
                print(
                    f"   Resposta (trecho): {webhook_corpo[:500]}{'…' if len(webhook_corpo) > 500 else ''}"
                )
            if webhook_status not in (200, 201, 204):
                print("   ⚠️ Webhook pode ter falhado — confira status e resposta.")
        else:
            print("\n⏭️ Webhook desativado")

        if no_wait:
            try:
                driver.quit()
            except Exception:
                pass
            print("\n✅ Encerrado (no_wait).")
            tem_ganho = bool(dados.get("ganho_geral_mes_passado"))
            tem_taxas = bool(avisos.get("taxas_ok"))
            sucesso_real = tem_ganho or tem_taxas
            msg = "Fluxo financeiro concluído."
            if not sucesso_real:
                msg = (
                    "Login OK, mas não foram obtidos ganhos nem taxas. "
                    "Verifique avisos_extracao nos dados e os logs do container (painel / créditos)."
                )
            elif not tem_ganho:
                msg = "Parcial: taxas OK; ganhos gerais não extraídos."
            elif not tem_taxas:
                msg = "Parcial: ganhos OK; taxas não extraídas."
            return {
                "sucesso": sucesso_real,
                "mensagem": msg,
                "email": email,
                "chave_totp": "",
                "dados_extraidos": dados,
                "payload": payload_webhook,
                "arquivo_json": DADOS_FILE,
                "webhook_enviado": enviar_webhook,
                "webhook_http_status": webhook_status,
                "webhook_resposta_trecho": (webhook_corpo or "")[:500] if webhook_corpo else None,
            }

        _drivers_abertos[email] = driver
        print("\n🌐 Navegador PERMANECE ABERTO (Ctrl+C para sair)")

        while True:
            time.sleep(60)
            try:
                driver.current_url
            except Exception:
                break
        return None

    except KeyboardInterrupt:
        print("\n⚠️ Interrompido")
        try:
            driver.quit()
        except Exception:
            pass
        return {
            "sucesso": False,
            "mensagem": "Interrompido pelo usuário",
            "email": email,
            "chave_totp": "",
        }
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        import traceback

        traceback.print_exc()
        try:
            driver.quit()
        except Exception:
            pass
        return {
            "sucesso": False,
            "mensagem": str(e),
            "email": email,
            "chave_totp": "",
        }


def main():
    parser = argparse.ArgumentParser(description="Fluxo financeiro TaxiMachine → JSON → webhook")
    parser.add_argument("--headless", action="store_true", help="Chrome sem janela")
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Encerra o Chrome após enviar o webhook (não fica em loop)",
    )
    parser.add_argument(
        "--sem-webhook",
        action="store_true",
        help="Só grava JSON local, não faz POST",
    )
    args = parser.parse_args()

    email = os.environ.get("FIN_EMAIL", DEFAULT_EMAIL)
    senha = os.environ.get("FIN_SENHA", DEFAULT_SENHA)

    executar_fluxo_financeiro_completo(
        email,
        senha,
        headless=args.headless,
        no_wait=args.no_wait,
        enviar_webhook=not args.sem_webhook,
    )


if __name__ == "__main__":
    main()
