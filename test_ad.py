import time
import os
from auto_2fa import criar_driver, fazer_login, inserir_codigo_login_2fa, obter_chave, detectar_cenario_pos_login, navegar_recursos_premium
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def teste_ad_motorista():
    email = "1Fidelidade@ubizcar.com"
    senha = "Gina2405alecio@10"
    chave = obter_chave(email)

    driver = criar_driver(headless=False)
    fazer_login(driver, email, senha)
    time.sleep(3)
    cenario = detectar_cenario_pos_login(driver)
    if cenario in ("login_2fa", "setup_2fa"):
        inserir_codigo_login_2fa(driver, chave)
        time.sleep(5)

    navegar_recursos_premium(driver)
    time.sleep(3)

    print("[INFO] Configurando anúncio na tela inicial do app motorista")
    
    # 1. Clicar em 'Sim'
    try:
        elm_sim = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "AnuncioAppTaxista_exibir_anuncio_0"))
        )
        driver.execute_script("arguments[0].click();", elm_sim)
        print("Clicou em 'Sim'")
    except Exception as e:
        print(f"Erro em 'Sim': {e}")

    # 2. Clicar em Adicionar novo anúncio (se a img input não existir)
    time.sleep(1)
    file_input_id = "upload-anuncio-tela_inicial_app_taxista-0"
    try:
        driver.find_element(By.ID, file_input_id)
        print("Input de arquivo já existe.")
    except:
        print("Input não existe. Clicando em Adicionar novo anúncio.")
        try:
            add_btn = driver.find_element(By.ID, "adicionar-novo-anuncio-tela_inicial_app_taxista")
            driver.execute_script("arguments[0].click();", add_btn)
            time.sleep(2)
        except Exception as e:
            print(f"Erro ao adicionar novo anúncio: {e}")

    # 3. Adicionar uma imagem de teste
    try:
        # Criar uma imagem fake
        fake_img_path = os.path.abspath("fake_ad.png")
        if not os.path.exists(fake_img_path):
            from PIL import Image
            img = Image.new('RGB', (640, 275), color = 'red')
            img.save(fake_img_path)
            
        file_input = driver.find_element(By.ID, file_input_id)
        file_input.send_keys(fake_img_path)
        print("Imagem enviada.")
        time.sleep(3) # Aguardar envio
    except Exception as e:
        print(f"Erro no envio da imagem: {e}")

    # 4. Inserir link
    try:
        link_input = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "AnuncioAppTaxista_0_url_anuncio"))
        )
        driver.execute_script("arguments[0].removeAttribute('disabled');", link_input)
        driver.execute_script("arguments[0].removeAttribute('readonly');", link_input)
        link_input.clear()
        link_input.send_keys("https://www.google.com")
        print("Link inserido.")
    except Exception as e:
        print(f"Erro no link: {e}")

    # 5. Selecionar todas as centrais
    try:
        select_id = "filtro_bandeiras_anuncio_tela_inicial_app_taxista_0"
        driver.execute_script(f"""
            var select = document.getElementById('{select_id}');
            if(select) {{
                var wrapper = select.nextElementSibling;
                if(wrapper) {{
                    var allCheck = wrapper.querySelector('input[value="multiselect-all"]');
                    if(allCheck && !allCheck.checked) {{
                        allCheck.click();
                        console.log("Selecionar todas clicado!");
                    }}
                }}
            }}
        """)
        print("Selecionar todas chamado via JS.")
    except Exception as e:
        print(f"Erro no selecionar todas: {e}")
        
    print("Teste finalizado. Parando o driver em 120s...")
    time.sleep(120)
    driver.quit()

if __name__ == "__main__":
    teste_ad_motorista()
