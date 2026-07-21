#!/usr/bin/env python3
"""Teste local: criar campanha corrida e remover em seguida."""
import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_2fa import executar_adicionar_campanha_corrida, executar_remover_campanha_corrida

DEFAULT_EMAIL = os.environ.get("TM_EMAIL", "1Fidelidade@ubizcar.com")
DEFAULT_SENHA = os.environ.get("TM_SENHA", "Gina2405alecio@10")
DEFAULT_CHAVE = os.environ.get("TM_TOTP", "VU3EDQM4TG7TDUCGBZTWQG5TAJCBSKFJ")
DEFAULT_LINK = "https://www.exemplo.com/campanha-corrida"
IMG_LOCAL = "/tmp/banner_corrida_test.png"


def _png_640x480(path: str) -> None:
    w, h = 640, 480
    rgb = (30, 144, 255)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes([rgb[0], rgb[1], rgb[2]] * w) for _ in range(h))
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(data)


def main():
    p = argparse.ArgumentParser(description="Criar + remover campanha corrida (TaxiMachine)")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--email", default=DEFAULT_EMAIL)
    p.add_argument("--senha", default=DEFAULT_SENHA)
    p.add_argument("--chave", default=DEFAULT_CHAVE)
    p.add_argument("--imagem", default=IMG_LOCAL)
    p.add_argument("--link", default=DEFAULT_LINK)
    p.add_argument("--limite", type=int, default=1000)
    args = p.parse_args()

    if not os.path.isfile(args.imagem):
        print(f"Gerando imagem de teste → {args.imagem}")
        _png_640x480(args.imagem)

    common = dict(
        email=args.email,
        senha=args.senha,
        chave_secreta=args.chave,
        headless=args.headless,
        manter_aberto=False,
    )

    print("\n========== 1/2 CRIAR banner corrida ==========")
    r1 = executar_adicionar_campanha_corrida(
        **common,
        imagem_path=args.imagem,
        link_campanha=args.link,
        selecionar_todas=True,
        limite_corridas=args.limite,
    )
    print("sucesso:", r1.get("sucesso"))
    print("mensagem:", r1.get("mensagem"))
    if r1.get("verificacao"):
        print("verificacao:", r1.get("verificacao"))

    if not r1.get("sucesso"):
        sys.exit(1)

    print("\n========== 2/2 REMOVER banner corrida ==========")
    r2 = executar_remover_campanha_corrida(**common, indice=None)
    print("sucesso:", r2.get("sucesso"))
    print("mensagem:", r2.get("mensagem"))

    sys.exit(0 if r2.get("sucesso") else 1)


if __name__ == "__main__":
    main()
