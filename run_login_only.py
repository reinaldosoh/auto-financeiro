import sys
import os
import time
sys.path.append("/Users/reinaldofc/Downloads/AUTO_FINANCEIRO")

from auto_2fa import executar_login, obter_chave

email = "1Fidelidade@ubizcar.com"
senha = "Gina2405alecio@10"
chave = obter_chave(email)

print(f"Iniciando login para {email} (Chave TOTP: {chave})")

ret = executar_login(
    email=email,
    senha=senha,
    headless=False,
    manter_aberto=True
)

print(f"Resultado do login: {ret}")

print("Navegador aberto e autenticado. O script ficará rodando em loop para manter a janela aberta. (Aperte Ctrl+C para encerrar)...")
try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    print("Saindo...")
