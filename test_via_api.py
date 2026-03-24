import requests
import base64
import json

imagem_path = "/tmp/cascade_img_1774325662220_1.png"
with open(imagem_path, "rb") as image_file:
    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

payload = {
    "email": "1Fidelidade@ubizcar.com",
    "senha": "Gina2405alecio@10",
    "headless": False,
    "manter_aberto": True,
    "imagem_base64": encoded_string,
    "link_anuncio": "https://meli.la/1uCEWgL",
    "selecionar_todas": True
}

print("Enviando requisição via API com base64 da imagem", imagem_path)
response = requests.post("http://127.0.0.1:8000/anuncio-motorista", json=payload)
print("Status code:", response.status_code)
print("Resposta:", json.dumps(response.json(), indent=2, ensure_ascii=False))
