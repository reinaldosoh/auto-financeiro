#!/usr/bin/env python3
"""
Cadastro de anúncio passageiro — execução local (sem subir a API).

Uso:
  python3 run_passageiro_local.py                    # Chrome visível (rode no Terminal.app, não no Cursor)
  python3 run_passageiro_local.py --headless         # sem janela
  python3 run_passageiro_local.py --headless --baixar-imagem

No Docker (igual produção): ./scripts/docker-test-passageiro.sh

Credenciais: TM_EMAIL, TM_SENHA, TM_TOTP ou flags --email --senha --chave.
"""
import argparse
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_2fa import executar_adicionar_anuncio_passageiro

DEFAULT_EMAIL = "1Fidelidade@ubizcar.com"
DEFAULT_SENHA = "Gina2405alecio@10"
DEFAULT_CHAVE = "VU3EDQM4TG7TDUCGBZTWQG5TAJCBSKFJ"
DEFAULT_IMG_URL = "https://txndikzivbfhkzhgkykp.supabase.co/storage/v1/object/public/banners/ANUNCIO.jpg"
DEFAULT_LINK = "https://meli.la/1uCEWgL"
IMG_LOCAL = "/tmp/ANUNCIO_PASS.jpg"


def main():
    p = argparse.ArgumentParser(description="Teste local anúncio passageiro (TaxiMachine)")
    p.add_argument("--headless", action="store_true", help="Rodar sem interface gráfica")
    p.add_argument("--manter-aberto", action="store_true", help="Não fechar o Chrome ao terminar")
    p.add_argument("--baixar-imagem", action="store_true", help="Baixar imagem da URL padrão para /tmp")
    p.add_argument("--email", default=os.environ.get("TM_EMAIL", DEFAULT_EMAIL))
    p.add_argument("--senha", default=os.environ.get("TM_SENHA", DEFAULT_SENHA))
    p.add_argument("--chave", default=os.environ.get("TM_TOTP", DEFAULT_CHAVE))
    p.add_argument("--imagem", default=IMG_LOCAL, help="Caminho local do JPEG")
    p.add_argument("--imagem-url", default=DEFAULT_IMG_URL)
    p.add_argument("--link", default=DEFAULT_LINK)
    args = p.parse_args()

    # Não remova DOCKER aqui: dentro do container a imagem define DOCKER=true e o Chrome usa Xvfb (:99).
    # No Mac, forçar modo “sem Docker” se você exportou DOCKER por engano:
    if os.environ.get("TM_FORCE_LOCAL_CHROME", "").lower() in ("1", "true", "yes"):
        os.environ.pop("DOCKER", None)

    if args.baixar_imagem or not os.path.isfile(args.imagem):
        print(f"Baixando imagem → {args.imagem}")
        urllib.request.urlretrieve(args.imagem_url, args.imagem)

    print(
        f"Fluxo passageiro | headless={args.headless} | imagem={args.imagem} | link={args.link}\n"
    )

    r = executar_adicionar_anuncio_passageiro(
        email=args.email,
        senha=args.senha,
        chave_secreta=args.chave,
        headless=args.headless,
        imagem_path=args.imagem,
        link_anuncio=args.link,
        selecionar_todas=True,
        manter_aberto=args.manter_aberto,
    )

    print("\n========== RESULTADO ==========")
    print("sucesso: ", r.get("sucesso"))
    print("mensagem:", r.get("mensagem"))
    if r.get("verificacao"):
        print("verificacao:", r.get("verificacao"))
    print("================================\n")

    if not args.headless and r.get("sucesso"):
        print("Chrome visível: feche a janela quando quiser (ou use sem --manter-aberto na próxima).")
        try:
            time.sleep(180)
        except KeyboardInterrupt:
            pass
    elif not args.headless and not r.get("sucesso"):
        print("Falhou. Janela aberta ~90s para inspeção (Ctrl+C encerra espera).")
        try:
            time.sleep(90)
        except KeyboardInterrupt:
            pass

    sys.exit(0 if r.get("sucesso") else 1)


if __name__ == "__main__":
    main()
