import sys
import os
sys.path.append("/Users/reinaldofc/Downloads/AUTO_FINANCEIRO")

from auto_2fa import executar_adicionar_anuncio_motorista, obter_chave

email = "1Fidelidade@ubizcar.com"
senha = "Gina2405alecio@10"
chave = obter_chave(email)
imagem = "/tmp/cascade_img_1774350032659_1.jpeg"
link = "https://meli.la/1uCEWgL"

print(f"Executando com chave: {chave}, imagem: {imagem}, link: {link}")

ret = executar_adicionar_anuncio_motorista(
    email=email,
    senha=senha,
    chave_secreta=chave,
    headless=False,
    imagem_path=imagem,
    link_anuncio=link,
    selecionar_todas=True,
    manter_aberto=False
)

print(f"Resultado: {ret}")
