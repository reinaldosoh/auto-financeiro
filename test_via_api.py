import requests
import base64
import json
import sys

BASE_URL = "https://reinaldo-automachine.sw5bxa.easypanel.host"

EMAIL = "1Fidelidade@ubizcar.com"
SENHA = "Gina2405alecio@10"
CHAVE_SECRETA = "VU3EDQM4TG7TDUCGBZTWQG5TAJCBSKFJ"


def cadastrar_banner(imagem_path: str, link_anuncio: str):
    """Chama POST /anuncio-motorista para cadastrar o banner."""
    with open(imagem_path, "rb") as f:
        imagem_b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "email": EMAIL,
        "senha": SENHA,
        "chave_secreta": CHAVE_SECRETA,
        "headless": True,
        "manter_aberto": False,
        "imagem_base64": imagem_b64,
        "link_anuncio": link_anuncio,
        "selecionar_todas": True,
    }

    print(f"\n[CADASTRAR BANNER] POST {BASE_URL}/anuncio-motorista")
    resp = requests.post(f"{BASE_URL}/anuncio-motorista", json=payload, timeout=300)
    print("Status:", resp.status_code)
    print("Resposta:", json.dumps(resp.json(), indent=2, ensure_ascii=False))
    return resp


def remover_banner():
    """Chama POST /remover-anuncio para remover o banner ativo."""
    payload = {
        "email": EMAIL,
        "senha": SENHA,
        "chave_secreta": CHAVE_SECRETA,
        "headless": True,
        "manter_aberto": False,
    }

    print(f"\n[REMOVER BANNER] POST {BASE_URL}/remover-anuncio")
    resp = requests.post(f"{BASE_URL}/remover-anuncio", json=payload, timeout=300)
    print("Status:", resp.status_code)
    print("Resposta:", json.dumps(resp.json(), indent=2, ensure_ascii=False))
    return resp


if __name__ == "__main__":
    acao = sys.argv[1] if len(sys.argv) > 1 else "remover"

    if acao == "cadastrar":
        imagem = sys.argv[2] if len(sys.argv) > 2 else "/tmp/banner.png"
        link   = sys.argv[3] if len(sys.argv) > 3 else "https://meli.la/1uCEWgL"
        cadastrar_banner(imagem, link)
    elif acao == "remover":
        remover_banner()
    else:
        print("Uso: python3 test_via_api.py [cadastrar <imagem_path> <link>] | [remover]")
