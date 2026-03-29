#!/usr/bin/env python3
"""
Login TaxiMachine com Chrome VISÍVEL e mantém o navegador aberto para inspeção manual.

Uso (Terminal.app, fora do Cursor):
  cd /caminho/AUTO_FINANCEIRO
  python3 run_login_visivel.py

Credenciais: TM_EMAIL, TM_SENHA, TM_TOTP ou flags --email --senha --chave.
Se a chave já estiver em chaves_totp.json, pode omitir --chave.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auto_2fa
from auto_2fa import (
    criar_driver,
    fazer_login,
    detectar_cenario_pos_login,
    inserir_codigo_login_2fa,
    navegar_recursos_premium,
    obter_chave,
    salvar_chave,
)

DEFAULT_EMAIL = "1Fidelidade@ubizcar.com"
DEFAULT_SENHA = "Gina2405alecio@10"
DEFAULT_CHAVE = "VU3EDQM4TG7TDUCGBZTWQG5TAJCBSKFJ"


def main():
    p = argparse.ArgumentParser(description="Login TaxiMachine — Chrome visível, navegador aberto")
    p.add_argument("--email", default=os.environ.get("TM_EMAIL", DEFAULT_EMAIL))
    p.add_argument("--senha", default=os.environ.get("TM_SENHA", DEFAULT_SENHA))
    p.add_argument(
        "--chave",
        default=os.environ.get("TM_TOTP") or DEFAULT_CHAVE,
        help="Segredo TOTP base32 (padrão dev; sobrescreva com TM_TOTP ou --chave)",
    )
    p.add_argument(
        "--salvar-chave",
        action="store_true",
        help="Grava --chave em chaves_totp.json para próximos logins",
    )
    p.add_argument(
        "--sem-recursos-premium",
        action="store_true",
        help="Para na tela pós-login (não abre Configurações > Recursos premium)",
    )
    args = p.parse_args()

    if os.environ.get("TM_FORCE_LOCAL_CHROME", "").lower() in ("1", "true", "yes"):
        os.environ.pop("DOCKER", None)

    email = args.email
    senha = args.senha
    chave = (args.chave or "").strip()
    if not chave:
        chave = obter_chave(email)

    driver = None
    try:
        print("Abrindo Chrome (visível)…\n")
        driver = criar_driver(headless=False)

        if not fazer_login(driver, email, senha):
            print("Falha no login (email/senha).")
            sys.exit(1)

        time.sleep(2)
        cenario = detectar_cenario_pos_login(driver)

        if cenario in ("login_2fa", "setup_2fa"):
            if not chave:
                print(
                    "Login pediu 2FA e não há chave. Use --chave SEU_BASE32 ou cadastre em chaves_totp.json."
                )
                sys.exit(1)
            if args.salvar_chave:
                salvar_chave(email, chave)
                print("Chave TOTP salva em chaves_totp.json.")
            if not inserir_codigo_login_2fa(driver, chave):
                print("Falha ao enviar código TOTP.")
                sys.exit(1)
            print("2FA aceito.")
            time.sleep(2)
        elif cenario != "logado":
            print(f"Cenário pós-login inesperado: {cenario}")
            sys.exit(1)

        if not args.sem_recursos_premium:
            time.sleep(1)
            if navegar_recursos_premium(driver):
                print("\n✅ Aberto em: Configurações > Gerais > Recursos premium")
            else:
                print("\n⚠️ Login OK, mas não consegui abrir Recursos premium — navegue manualmente.")
        else:
            print("\n✅ Login OK (parado na tela atual).")

        auto_2fa._drivers_abertos[email] = driver

        print(
            "\n>>> Navegador mantido ABERTO. Feche a janela do Chrome quando terminar.\n"
            ">>> Este script fica em espera (Ctrl+C só encerra o script, não mata o Chrome).\n"
        )
        try:
            while True:
                time.sleep(600)
        except KeyboardInterrupt:
            print("\nScript encerrado. Chrome continua aberto se você não fechou.")

    except Exception as e:
        print(f"Erro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
