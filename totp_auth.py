"""
Automação de autenticação TOTP (2FA) para sistemas que usam verificação em duas etapas.
Gera códigos de 6 dígitos compatíveis com Google Authenticator, Microsoft Authenticator, etc.
"""

import pyotp
import pyperclip
import time
import os
from datetime import datetime


def carregar_chave_secreta():
    """
    Carrega a chave secreta TOTP.
    Prioridade: variável de ambiente > arquivo .env > input manual.
    """
    # 1. Tenta variável de ambiente
    secret = os.environ.get("TOTP_SECRET")
    if secret:
        return secret.replace(" ", "").strip()

    # 2. Tenta arquivo .env na mesma pasta
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for linha in f:
                linha = linha.strip()
                if linha.startswith("TOTP_SECRET="):
                    secret = linha.split("=", 1)[1].strip().strip('"').strip("'")
                    return secret.replace(" ", "")

    # 3. Pede ao usuário
    print("⚠️  Chave secreta TOTP não encontrada.")
    print("   Configure a variável TOTP_SECRET no arquivo .env ou como variável de ambiente.")
    print("   A chave é aquela exibida na tela de configuração do 2FA (ex: 4C2O Y7BF JOAC WFEA ...)\n")
    secret = input("Cole a chave secreta TOTP aqui: ").strip()
    return secret.replace(" ", "")


def gerar_codigo_totp(secret: str) -> str:
    """Gera o código TOTP de 6 dígitos atual."""
    totp = pyotp.TOTP(secret)
    return totp.now()


def tempo_restante() -> int:
    """Retorna quantos segundos faltam para o código atual expirar."""
    return 30 - (int(time.time()) % 30)


def main():
    print("=" * 50)
    print("  GERADOR DE CÓDIGO TOTP (2FA)")
    print("=" * 50)

    secret = carregar_chave_secreta()

    if not secret:
        print("❌ Nenhuma chave secreta fornecida. Encerrando.")
        return

    # Valida a chave
    try:
        totp = pyotp.TOTP(secret)
        codigo = totp.now()
    except Exception as e:
        print(f"❌ Chave secreta inválida: {e}")
        return

    print(f"\n✅ Chave secreta carregada com sucesso!")
    print(f"   Algoritmo: TOTP (SHA1, 30s, 6 dígitos)\n")

    while True:
        codigo = gerar_codigo_totp(secret)
        restante = tempo_restante()

        # Copia automaticamente para a área de transferência
        try:
            pyperclip.copy(codigo)
            copiado = " (copiado para clipboard)"
        except Exception:
            copiado = ""

        print(f"🔑 Código: {codigo}  |  Expira em: {restante:2d}s{copiado}", end="\r")

        # Se faltar pouco tempo, espera o próximo ciclo
        if restante <= 1:
            time.sleep(1.5)
            print()  # Nova linha quando o código mudar
        else:
            time.sleep(1)


def gerar_codigo_unico(secret: str = None) -> str:
    """
    Função utilitária para ser importada por outros scripts.
    Retorna o código TOTP atual como string.
    """
    if secret is None:
        secret = carregar_chave_secreta()
    return gerar_codigo_totp(secret)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Encerrado pelo usuário.")
