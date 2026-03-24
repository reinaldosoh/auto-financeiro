import sys
import os
sys.path.append("/Users/reinaldofc/Downloads/AUTO_FINANCEIRO")

from auto_2fa import executar_remover_anuncio_motorista

email = "1Fidelidade@ubizcar.com"
senha = "Gina2405alecio@10"

print(f"Executando remoção de anúncio para: {email}")

ret = executar_remover_anuncio_motorista(
    email=email,
    senha=senha,
    headless=False,
    manter_aberto=True
)

print(f"Resultado: {ret}")
