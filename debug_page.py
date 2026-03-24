"""Script de diagnóstico: lista todos os inputs e botões da página de login do TaxiMachine."""

import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

URL = "https://cloud.taximachine.com.br/"

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1280,900")

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=opts)

print(f"Acessando {URL}...")
driver.get(URL)
time.sleep(5)

print(f"\n=== URL atual: {driver.current_url} ===")
print(f"=== Título: {driver.title} ===\n")

# Lista todos os inputs
print("=" * 60)
print("TODOS OS INPUTS:")
print("=" * 60)
inputs = driver.find_elements(By.TAG_NAME, "input")
for i, inp in enumerate(inputs):
    print(f"  [{i}] type={inp.get_attribute('type')!r} "
          f"name={inp.get_attribute('name')!r} "
          f"id={inp.get_attribute('id')!r} "
          f"placeholder={inp.get_attribute('placeholder')!r} "
          f"class={inp.get_attribute('class')!r} "
          f"visible={inp.is_displayed()}")

# Lista todos os botões
print("\n" + "=" * 60)
print("TODOS OS BUTTONS:")
print("=" * 60)
buttons = driver.find_elements(By.TAG_NAME, "button")
for i, btn in enumerate(buttons):
    print(f"  [{i}] text={btn.text!r} "
          f"type={btn.get_attribute('type')!r} "
          f"id={btn.get_attribute('id')!r} "
          f"class={btn.get_attribute('class')!r} "
          f"visible={btn.is_displayed()}")

# Lista links que podem ser botões
print("\n" + "=" * 60)
print("LINKS (a tags):")
print("=" * 60)
links = driver.find_elements(By.TAG_NAME, "a")
for i, a in enumerate(links):
    if a.text.strip():
        print(f"  [{i}] text={a.text.strip()!r} href={a.get_attribute('href')!r} class={a.get_attribute('class')!r}")

# Forms
print("\n" + "=" * 60)
print("FORMS:")
print("=" * 60)
forms = driver.find_elements(By.TAG_NAME, "form")
for i, f in enumerate(forms):
    print(f"  [{i}] action={f.get_attribute('action')!r} method={f.get_attribute('method')!r} id={f.get_attribute('id')!r}")

# Iframes
print("\n" + "=" * 60)
print("IFRAMES:")
print("=" * 60)
iframes = driver.find_elements(By.TAG_NAME, "iframe")
for i, ifr in enumerate(iframes):
    print(f"  [{i}] src={ifr.get_attribute('src')!r} id={ifr.get_attribute('id')!r} name={ifr.get_attribute('name')!r}")

# Dump parte do HTML do body
print("\n" + "=" * 60)
print("HTML DO BODY (primeiros 5000 chars):")
print("=" * 60)
body = driver.find_element(By.TAG_NAME, "body")
print(body.get_attribute("innerHTML")[:5000])

driver.quit()
print("\n✅ Diagnóstico concluído.")
