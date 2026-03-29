#!/usr/bin/env python3
"""
Remove anúncio(s) passageiro — local ou Docker (sem subir a API).

Uso:
  python3 run_remover_passageiro_local.py --indice 1      # mesmo valor que dom_slot_idx do disparo
  python3 run_remover_passageiro_local.py --headless --indice 0
  python3 run_remover_passageiro_local.py               # remove todos (cuidado)

Docker: ./scripts/docker-test-remover-passageiro.sh
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_2fa import executar_remover_anuncio_passageiro

DEFAULT_EMAIL = "1Fidelidade@ubizcar.com"
DEFAULT_SENHA = "Gina2405alecio@10"
DEFAULT_CHAVE = "VU3EDQM4TG7TDUCGBZTWQG5TAJCBSKFJ"


def main():
    p = argparse.ArgumentParser(description="Remover anúncio passageiro (TaxiMachine)")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--manter-aberto", action="store_true")
    p.add_argument("--email", default=os.environ.get("TM_EMAIL", DEFAULT_EMAIL))
    p.add_argument("--senha", default=os.environ.get("TM_SENHA", DEFAULT_SENHA))
    p.add_argument("--chave", default=os.environ.get("TM_TOTP") or DEFAULT_CHAVE)
    p.add_argument(
        "--indice",
        type=int,
        default=None,
        help="dom_slot_idx do disparo ou posição 0-based. Omitir = remover todos.",
    )
    args = p.parse_args()

    if os.environ.get("TM_FORCE_LOCAL_CHROME", "").lower() in ("1", "true", "yes"):
        os.environ.pop("DOCKER", None)

    r = executar_remover_anuncio_passageiro(
        email=args.email,
        senha=args.senha,
        chave_secreta=args.chave,
        headless=args.headless,
        manter_aberto=args.manter_aberto,
        indice=args.indice,
    )
    print("sucesso: ", r.get("sucesso"))
    print("mensagem:", r.get("mensagem"))
    sys.exit(0 if r.get("sucesso") else 1)


if __name__ == "__main__":
    main()
