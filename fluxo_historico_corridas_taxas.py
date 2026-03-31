#!/usr/bin/env python3
"""
Histórico de corridas (mês anterior) + taxas central e seguro (créditos) + webhook.

1) Login + 2FA (setup se necessário)
2) /solicitacao/historicoCorridas2 — Filtro lateral — datas mês anterior 00:00–23:59 — Filtrar
3) Extrai total de "Exibindo 1-30 de N resultados"
4) /bandeira/creditos — taxa_central e taxa_seguro_app
5) POST JSON no webhook (teste default)

Uso:
  python3 fluxo_historico_corridas_taxas.py --headless --no-wait
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fluxo_financeiro_completo import (
    enviar_json_webhook,
    extrair_taxas,
    fazer_login_completo,
)
from auto_2fa import criar_driver, _drivers_abertos

DEFAULT_EMAIL = os.environ.get("FIN_EMAIL", "Automacaofinanceiro@araxa.com")
DEFAULT_SENHA = os.environ.get("FIN_SENHA", "Reinaldo2026@")
DEFAULT_WEBHOOK = (
    "https://api-webhook.estruturadeapi.com/webhook/"
    "5c721c68-e792-4a3b-b7d8-5464b3cf3a65"
)
URL_HISTORICO = (
    "https://cloud.taximachine.com.br/solicitacao/historicoCorridas2?resetSesion=1"
)
DADOS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "dados_historico_corridas_taxas.json",
)


def periodo_mes_anterior() -> tuple[str, str, str, str]:
    """Retorna (data_ini, data_fim) DD/MM/YYYY e horas fixas 00:00 / 23:59."""
    hoje = date.today()
    primeiro_atual = hoje.replace(day=1)
    ultimo_anterior = primeiro_atual - timedelta(days=1)
    primeiro_anterior = ultimo_anterior.replace(day=1)
    d_ini = primeiro_anterior.strftime("%d/%m/%Y")
    d_fim = ultimo_anterior.strftime("%d/%m/%Y")
    return d_ini, "00:00", d_fim, "23:59"


def _preencher_input(driver, el, texto: str) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.15)
    try:
        el.click()
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        driver.execute_script("arguments[0].value='';", el)
    el.send_keys(texto)


def _encontrar_input_por_rotulo(driver, rotulo: str):
    from selenium.webdriver.common.by import By

    rot_esc = rotulo.replace("'", "\\'")
    xpaths = [
        f"//label[contains(normalize-space(.), '{rot_esc}')]/following::input[not(@type='hidden')][1]",
        f"//*[self::p or self::span or self::label][contains(normalize-space(.), '{rot_esc}')]/following::input[not(@type='hidden')][1]",
        f"//*[contains(normalize-space(.), '{rot_esc}')]/ancestor::div[contains(@class,'field') or contains(@class,'input')][1]//input[not(@type='hidden')]",
    ]
    from selenium.common.exceptions import NoSuchElementException

    for xp in xpaths:
        try:
            el = driver.find_element(By.XPATH, xp)
            if el.is_displayed():
                return el
        except NoSuchElementException:
            continue
    return None


def _clicar_botao_filtro_lateral(driver) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    wait = WebDriverWait(driver, 25)
    # Botão principal "Filtro" (não "Filtrar")
    candidatos = [
        (By.XPATH, "//button[normalize-space()='Filtro']"),
        (By.XPATH, "//button[contains(@class,'btn') and normalize-space()='Filtro']"),
        (By.XPATH, "//a[normalize-space()='Filtro']"),
        (By.PARTIAL_LINK_TEXT, "Filtro"),
    ]
    for by, sel in candidatos:
        try:
            el = wait.until(EC.element_to_be_clickable((by, sel)))
            if "filtrar" in (el.text or "").lower() and (el.text or "").strip().lower() != "filtro":
                continue
            driver.execute_script("arguments[0].click();", el)
            time.sleep(1)
            return True
        except Exception:
            continue
    return False


def _aguardar_painel_filtro(driver, timeout: float = 20) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    try:
        WebDriverWait(driver, timeout).until(
            EC.any_of(
                EC.visibility_of_element_located(
                    (By.XPATH, "//*[contains(.,'Filtro de pesquisa')]")
                ),
                EC.visibility_of_element_located(
                    (By.XPATH, "//*[contains(.,'Data inicial')]")
                ),
            )
        )
        return True
    except Exception:
        return False


def _clicar_filtrar(driver) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    wait = WebDriverWait(driver, 15)
    try:
        btn = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[contains(normalize-space(.),'Filtrar') and not(contains(normalize-space(.),'Limpar'))]",
                )
            )
        )
        driver.execute_script("arguments[0].click();", btn)
        return True
    except Exception:
        try:
            btn = driver.find_element(
                By.XPATH,
                "//button[contains(translate(.,'FILTRAR','filtrar'),'filtrar')]",
            )
            driver.execute_script("arguments[0].click();", btn)
            return True
        except Exception:
            return False


def _aguardar_fim_overlay_filtrando(driver, timeout: int = 300) -> None:
    """Espera overlay 'Filtrando' aparecer (opcional) e sumir."""
    from selenium.webdriver.common.by import By

    fim = time.time() + min(30, timeout)
    while time.time() < fim:
        try:
            els = driver.find_elements(By.XPATH, "//*[contains(text(),'Filtrando')]")
            if any(e.is_displayed() for e in els):
                break
        except Exception:
            pass
        time.sleep(0.3)
    # Espera sumir
    fim2 = time.time() + timeout
    while time.time() < fim2:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            if "Filtrando" not in body:
                time.sleep(1.2)
                body2 = driver.find_element(By.TAG_NAME, "body").text
                if "Filtrando" not in body2:
                    return
        except Exception:
            return
        time.sleep(0.5)


def extrair_total_corridas_resultados(driver, timeout: int = 300) -> Optional[int]:
    from selenium.webdriver.common.by import By

    pat = re.compile(
        r"Exibindo\s+\d+\s*[-–]\s*\d+\s+de\s+([\d.]+)\s+resultados",
        re.IGNORECASE,
    )
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            texto = driver.find_element(By.TAG_NAME, "body").text
            m = pat.search(texto)
            if m:
                n = m.group(1).replace(".", "")
                return int(n)
        except Exception:
            pass
        time.sleep(0.8)
    m = pat.search(driver.page_source)
    if m:
        return int(m.group(1).replace(".", ""))
    return None


def fluxo_historico_filtrar_mes_anterior(driver) -> Optional[int]:
    from selenium.webdriver.common.by import By

    print("\n📋 Histórico de corridas — mês anterior")
    driver.get(URL_HISTORICO)
    time.sleep(4)

    if not _clicar_botao_filtro_lateral(driver):
        print("   ⚠️ Botão 'Filtro' não encontrado")
        return None

    if not _aguardar_painel_filtro(driver):
        print("   ⚠️ Painel de filtro não abriu")
        return None

    d_ini, h_ini, d_fim, h_fim = periodo_mes_anterior()
    print(f"   📅 Período: {d_ini} {h_ini} → {d_fim} {h_fim}")

    campos = [
        ("Data inicial", d_ini),
        ("Hora inicial", h_ini),
        ("Data final", d_fim),
        ("Hora final", h_fim),
    ]
    for rotulo, valor in campos:
        inp = _encontrar_input_por_rotulo(driver, rotulo)
        if not inp:
            print(f"   ⚠️ Campo não encontrado: {rotulo}")
            return None
        _preencher_input(driver, inp, valor)
        time.sleep(0.2)

    if not _clicar_filtrar(driver):
        print("   ⚠️ Botão 'Filtrar' não encontrado")
        return None

    print("   ⏳ Aguardando filtragem…")
    _aguardar_fim_overlay_filtrando(driver, timeout=300)

    total = extrair_total_corridas_resultados(driver, timeout=120)
    if total is not None:
        print(f"   ✅ Total de corridas: {total}")
    else:
        print("   ⚠️ Não foi possível ler o total de resultados")
    return total


def extrair_taxas_central_e_seguro(driver) -> Dict[str, Any]:
    todas = extrair_taxas(driver)
    out: Dict[str, Any] = {}
    for k in ("taxa_central", "taxa_seguro_app"):
        if k in todas:
            out[k] = todas[k]
    return out


def executar_fluxo_historico_corridas_taxas(
    email: str,
    senha: str,
    *,
    headless: bool = False,
    no_wait: bool = True,
    enviar_webhook: bool = True,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    wh = webhook_url or os.environ.get(
        "FIN_HISTORICO_WEBHOOK_URL", DEFAULT_WEBHOOK
    )

    print("=" * 70)
    print("FLUXO: histórico corridas (mês ant.) + taxas central/seguro")
    print("=" * 70)

    driver = criar_driver(headless=headless)
    d_ini, h_ini, d_fim, h_fim = periodo_mes_anterior()

    try:
        if not fazer_login_completo(driver, email, senha):
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

        total = fluxo_historico_filtrar_mes_anterior(driver)

        print("\n💳 Taxas (central + seguro app)")
        taxas = extrair_taxas_central_e_seguro(driver)

        from datetime import datetime

        dados_extraidos: Dict[str, Any] = {
            "total_corridas_mes_anterior": total,
            "periodo_filtro": {
                "data_inicial": d_ini,
                "hora_inicial": h_ini,
                "data_final": d_fim,
                "hora_final": h_fim,
            },
            "url_historico": URL_HISTORICO,
            "taxas": taxas,
            "data_extracao": datetime.now().isoformat(),
            "email_conta_taximachine": email,
        }

        payload = {
            "evento": "taximachine_historico_corridas_taxas",
            "extraido_em": dados_extraidos["data_extracao"],
            "email_conta": email,
            "dados_extraidos": dados_extraidos,
        }

        with open(DADOS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        print(f"\n📁 JSON: {DADOS_FILE}")

        wh_status = None
        wh_corpo = None
        if enviar_webhook:
            print("\n📤 Webhook…")
            wh_status, wh_corpo = enviar_json_webhook(wh, payload)
            print(f"   HTTP {wh_status}")
            if wh_corpo:
                print(f"   {wh_corpo[:400]}")

        resultado = {
            "sucesso": total is not None,
            "mensagem": "Concluído"
            if total is not None
            else "Falha ao obter total de corridas (taxas podem ter sido lidas)",
            "email": email,
            "chave_totp": "",
            "dados_extraidos": dados_extraidos,
            "payload": payload,
            "arquivo_json": DADOS_FILE,
            "webhook_enviado": enviar_webhook,
            "webhook_http_status": wh_status,
            "webhook_resposta_trecho": (wh_corpo or "")[:500] if wh_corpo else None,
        }

        if no_wait:
            try:
                driver.quit()
            except Exception:
                pass
            return resultado

        _drivers_abertos[email] = driver
        print("\n🌐 Navegador PERMANECE ABERTO (feche a janela ou encerre o processo)")
        while True:
            time.sleep(60)
            try:
                driver.current_url
            except Exception:
                break
        return None

    except Exception as e:
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
    p = argparse.ArgumentParser()
    p.add_argument("--headless", action="store_true")
    p.add_argument("--no-wait", action="store_true", help="Encerra o Chrome ao terminar")
    p.add_argument("--sem-webhook", action="store_true")
    args = p.parse_args()

    executar_fluxo_historico_corridas_taxas(
        DEFAULT_EMAIL,
        DEFAULT_SENHA,
        headless=args.headless,
        no_wait=args.no_wait,
        enviar_webhook=not args.sem_webhook,
    )


if __name__ == "__main__":
    main()
