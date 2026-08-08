# Guia de integração — API de Banners TaxiMachine (HTTP)

Documento para **outro time** que usa o painel TaxiMachine e precisa:

1. Subir um **servidor próprio** com API REST.
2. Chamar os **endpoints internos** do painel (não a API pública `integracao/v1`).
3. Resolver **2FA (TOTP)** de forma automática, **sem Selenium** no fluxo diário.

> **Painel base:** `https://cloud.taximachine.com.br`  
> **Referência de código:** [reinaldosoh/auto-financeiro](https://github.com/reinaldosoh/auto-financeiro)  
> **Campos do formulário:** [REFERENCIA_CAMPOS_PAINEL.md](./REFERENCIA_CAMPOS_PAINEL.md)

---

## Sumário

1. [Contexto e tipos de banner](#1-contexto-e-tipos-de-banner)
2. [Arquitetura recomendada](#2-arquitetura-recomendada)
3. [Pré-requisitos da conta Machine](#3-pré-requisitos-da-conta-machine)
4. [O que criar no servidor de vocês](#4-o-que-criar-no-servidor-de-vocês)
5. [Autenticação e 2FA (detalhado)](#5-autenticação-e-2fa-detalhado)
6. [Endpoints internos do painel Machine](#6-endpoints-internos-do-painel-machine)
7. [Fluxo por tipo de banner](#7-fluxo-por-tipo-de-banner)
8. [API REST sugerida para expor](#8-api-rest-sugerida-para-expor)
9. [Implementação passo a passo (Python)](#9-implementação-passo-a-passo-python)
10. [Persistência e segurança](#10-persistência-e-segurança)
11. [Troubleshooting](#11-troubleshooting)
12. [Selenium vs HTTP — quando usar cada um](#12-selenium-vs-http--quando-usar-cada-um)

---

## 1. Contexto e tipos de banner

Todos os banners abaixo ficam na **mesma tela** do painel:

**Configurações → Gerais → aba Recursos premium** (`/bandeira/update`)

| Recurso | Onde aparece no app | Identificador no painel |
|---------|---------------------|-------------------------|
| **Anúncio motorista** | Tela inicial do app **motorista** | `AnuncioAppTaxista` / `tela_inicial_app_taxista` |
| **Anúncio passageiro** | Tela inicial do app **passageiro** (até 3 slots) | `AnuncioTelaInicialAppPass` / `tela_inicial_app_passageiro` |
| **Campanha ciclo da corrida** | **Durante a corrida** (passageiro) | `Campanha` / seção campanha app passageiro |

**Importante:** não existe endpoint público documentado pela Machine para esses recursos. A automação replica o que o browser faz: login por cookie + chamadas XHR/form POST.

---

## 2. Arquitetura recomendada

```
┌──────────────────┐     HTTPS      ┌─────────────────────────────┐
│  Seu produto     │ ──────────────►│  Servidor de automação      │
│  (app, n8n, etc) │                │  (FastAPI / Node / etc.)    │
└──────────────────┘                │                             │
                                    │  • login_painel()           │
                                    │  • session_token (~30 min)  │
                                    │  • chaves_totp.json         │
                                    │  • machine_bandeira_http    │
                                    └──────────────┬──────────────┘
                                                   │ HTTPS + cookie
                                                   ▼
                                    ┌─────────────────────────────┐
                                    │  cloud.taximachine.com.br   │
                                    │  /site/login                │
                                    │  /site/verificar2FA         │
                                    │  /bandeira/salvarImagem...  │
                                    │  /bandeira/update           │
                                    └─────────────────────────────┘
```

**Princípios:**

- **Login uma vez** por operação (ou reutilize `session_token` por ~30 min).
- **TOTP automático** com segredo salvo (`chave_secreta` / `chaves_totp.json`).
- **Zero browser** no fluxo normal — só `requests` (ou equivalente).
- Selenium fica opcional **apenas** para cadastro inicial de 2FA em contas novas.

---

## 3. Pré-requisitos da conta Machine

| Requisito | Detalhe |
|-----------|---------|
| **Permissão de bandeira** | A conta deve acessar `/bandeira/update` sem HTTP 403. Contas só de integração/notificação podem não ter essa permissão. |
| **2FA já configurado** | Login HTTP exige TOTP ativo. Contas sem 2FA retornam `cadastrar2FA: true` — veja [seção 5.4](#54-cadastro-inicial-de-2fa-conta-nova). |
| **Recurso premium contratado** | Campanha no ciclo da corrida é recurso pago; a seção só aparece se a bandeira tiver o plano. |
| **Segredo TOTP disponível** | Obter uma vez do Authenticator ou do setup inicial; guardar em cofre (Supabase, Vault, `.env` cifrado). |

---

## 4. O que criar no servidor de vocês

### 4.1 Stack mínima (somente HTTP — recomendado)

| Componente | Sugestão |
|------------|----------|
| Runtime | Python 3.11+ (ou Node 20+ com `axios` + `otplib`) |
| Framework API | FastAPI + Uvicorn (ou Express/Nest) |
| HTTP client | `requests` / `httpx` |
| TOTP | `pyotp` |
| Persistência | Arquivo JSON ou banco para `chave_secreta` por email |

**Dependências Python (mínimo):**

```txt
fastapi>=0.104.0
uvicorn>=0.24.0
pydantic>=2.5.0
requests>=2.31.0
pyotp==2.9.0
```

> **Não precisa** de Chrome, Selenium, Xvfb se vocês implementarem só o fluxo HTTP.

### 4.2 Módulos sugeridos no código

| Módulo | Responsabilidade |
|--------|------------------|
| `machine_auth_http.py` | Login, `verificar2FA`, `autenticarUsuario2FA`, pool de sessões |
| `machine_bandeira_http.py` | GET/POST `/bandeira/update`, upload imagem, criar/remover banners |
| `api_server.py` | Rotas REST que seu produto consome |
| `chaves_totp.json` | Mapa `{ "email@x.com": "BASE32SECRET" }` (volume persistente) |

No repo de referência, `machine_notificacao_http.py` **já implementa** `login_painel()` e `autenticar_acao_2fa()`. Vocês podem copiar/adaptar esse arquivo.

### 4.3 Deploy

Exemplo Docker simplificado (sem Chrome):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Produção:**

- Expor HTTPS (Easypanel, Railway, ECS, etc.).
- Volume persistente em `/app/chaves_totp.json` (ou secret manager).
- Timeout do proxy ≥ **300 s** (upload + save pode demorar).
- Variável `PORT` conforme o host.

### 4.4 O que **não** é necessário

- API `integracao/v1/notificacao/push` — **não** controla banners de Recursos Premium.
- Browser headless — só se mantiverem rotas Selenium legadas.
- Enviar senha em toda chamada — após login, use só `session_token`.

---

## 5. Autenticação e 2FA (detalhado)

### 5.1 Visão geral

O painel Machine usa **TOTP** (Google Authenticator, etc.). Não dá para “desligar” o 2FA na API — vocês **automatizam** gerando o código de 6 dígitos a partir do **segredo Base32**.

Existem **duas camadas** de 2FA:

| Camada | Quando | Endpoint Machine |
|--------|--------|------------------|
| **Login** | Após email/senha | `POST /site/verificar2FA` |
| **Ação sensível** | Ao gravar/enviar certas ações | `POST /site/autenticarUsuario2FA` |

Banners em `/bandeira/update` usam pelo menos a camada de **login**. Algumas contas podem exigir também **ação sensível** ao gravar — tratem igual à notificação em massa.

### 5.2 Fluxo de login HTTP

```http
POST https://cloud.taximachine.com.br/site/login
Content-Type: application/x-www-form-urlencoded

LoginForm[username]=operador@empresa.com
LoginForm[password]=********
LoginForm[rememberMe]=0
```

**Resposta JSON — cenários:**

| Campo na resposta | Significado | Próximo passo |
|-------------------|-------------|---------------|
| `success: true` | Login OK (raro sem 2FA) | Usar cookie `PHPSESSID` |
| `solicitarCodigo2FA: true` | Pediu código TOTP | Ir para passo 5.3 |
| `cadastrar2FA: true` | Conta nunca configurou 2FA | Ver [5.4](#54-cadastro-inicial-de-2fa-conta-nova) |

### 5.3 Verificar código TOTP (login)

Gerar código com `pyotp`:

```python
import pyotp

def gerar_codigo(chave_secreta: str) -> str:
    return pyotp.TOTP(chave_secreta.replace(" ", "")).now()
```

Enviar:

```http
POST https://cloud.taximachine.com.br/site/verificar2FA
Content-Type: application/x-www-form-urlencoded

code=123456
```

**Resposta esperada:** `{ "success": true }` · Cookie `PHPSESSID` válido na mesma `requests.Session`.

**Implementação de referência** (`machine_notificacao_http.py`):

```python
# Pseudocódigo equivalente ao repo
session = requests.Session()
session.post(BASE + "/site/login", data={...})
if data["solicitarCodigo2FA"]:
    code = pyotp.TOTP(chave_secreta).now()
    session.post(BASE + "/site/verificar2FA", data={"code": code})
# session.cookies["PHPSESSID"] pronto para /bandeira/*
```

### 5.4 Cadastro inicial de 2FA (conta nova)

Se `cadastrar2FA: true`, o login HTTP **para** — não há endpoint mapeado no repo para concluir o wizard só via HTTP.

**Opções (escolham uma):**

| Opção | Esforço | Selenium? |
|-------|---------|-----------|
| Operador configura 2FA manualmente no painel e copia o segredo | Baixo | Não |
| Rodar **uma vez** um script que conclui o wizard e grava o segredo | Médio | Sim (setup único) |
| Reverse-engineer endpoints de cadastro 2FA | Alto | Não |

Depois que o segredo existe, **nunca mais precisam de browser** para login.

### 5.5 Onde guardar o segredo TOTP

**Formato do arquivo `chaves_totp.json`:**

```json
{
  "operador@bandeira.com": "VU3EDQM4TG7TDUCGBZTWQG5TAJCBSKFJ"
}
```

| Armazenamento | Uso |
|---------------|-----|
| `chaves_totp.json` no servidor | Servidor gera código sozinho no login |
| Campo cifrado no banco (`automation_totp`) | Cliente envia `chave_secreta` no body do login |
| Secret manager (AWS/GCP) | Mesmo efeito, mais seguro |

**Regra:** volume persistente no deploy — redeploy sem volume **apaga** a chave e o login falha até reconfigurar.

### 5.6 2FA de ação sensível (ao gravar)

Usado hoje em notificação em massa; **reutilizem** para save de bandeira se o painel pedir:

```http
POST https://cloud.taximachine.com.br/site/autenticarUsuario2FA
Content-Type: application/x-www-form-urlencoded

code=654321
```

Referer sugerido: página de onde veio a ação (ex.: `/bandeira/update`).

**Algoritmo:**

```
POST /bandeira/update (save)
  → se resposta contém "autenticação necessária"
  → POST /site/autenticarUsuario2FA { code: TOTP }
  → repetir POST /bandeira/update
```

Mesmo segredo TOTP do login; código novo a cada 30 s.

### 5.7 Sessão no servidor de vocês

Após login bem-sucedido, guardem:

```json
{
  "session_token": "uuid-interno",
  "phpsessid": "...",
  "email": "operador@bandeira.com",
  "expira_em": "2026-08-07T18:30:00Z"
}
```

- **`session_token`**: ID que **seu produto** envia nas rotas seguintes.
- **`PHPSESSID`**: cookie real dentro da `requests.Session` (memória do servidor).
- **TTL recomendado:** 30 minutos (igual ao repo de referência).

---

## 6. Endpoints internos do painel Machine

Base: `https://cloud.taximachine.com.br`

### 6.1 Autenticação

| Método | Caminho | Body | Descrição |
|--------|---------|------|-----------|
| GET | `/` | — | Página inicial (aquecer cookies) |
| POST | `/site/login` | `LoginForm[username]`, `LoginForm[password]`, `LoginForm[rememberMe]=0` | Login |
| POST | `/site/verificar2FA` | `code` | Código TOTP pós-login |
| POST | `/site/autenticarUsuario2FA` | `code` | 2FA para ação sensível |

**Headers úteis em todas as chamadas autenticadas:**

```http
User-Agent: Mozilla/5.0 (compatible; SuaAutomacao/1.0)
X-Requested-With: XMLHttpRequest
Cookie: PHPSESSID=...
Referer: https://cloud.taximachine.com.br/bandeira/update
```

### 6.2 Bandeira / Recursos Premium

| Método | Caminho | Descrição |
|--------|---------|-----------|
| GET | `/bandeira/update` | Carrega formulário `#bandeira-form`, `bandeiraId`, campos hidden, estado atual |
| POST | `/bandeira/update` | **Gravar** configurações (equivalente ao botão Salvar) |
| POST | `/bandeira/salvarImagemConfiguracao` | Upload de imagem → retorna `urlS3` |

### 6.3 Upload de imagem

Ver tabela completa em [REFERENCIA_CAMPOS_PAINEL.md](./REFERENCIA_CAMPOS_PAINEL.md#4-upload-de-imagem-comum-aos-três).

Resumo:

| Recurso | `tipo` | `campo` |
|---------|--------|---------|
| Anúncio motorista / passageiro | `anuncio` | `anuncio` |
| Campanha ciclo da corrida | `campanha` | `campanha` |

Multipart: `foto`, `id` (= `bandeiraId`), `tipo`, `campo`.

### 6.4 Endpoints que **não** são banners

Documentados no repo para outros fluxos (mesma sessão cookie):

| Caminho | Uso |
|---------|-----|
| `/notificacao/create` | Push em massa |
| `/tarifaCategoria/dinamicaArea` | Tarifa dinâmica |

---

## 7. Fluxo por tipo de banner

Fluxo comum a **todos**:

```
1. login_painel(email, senha, chave_secreta)
2. GET /bandeira/update  → extrair bandeiraId + form baseline
3. POST /bandeira/salvarImagemConfiguracao  → urlS3
4. Montar campos do recurso no form
5. (Opcional) autenticarUsuario2FA se necessário
6. POST /bandeira/update  → persistir
7. (Opcional) GET /bandeira/update  → validar
```

### 7.1 Anúncio motorista

**Criar/substituir:**

1. Ativar: `AnuncioAppTaxista_exibir_anuncio_0 = 1`
2. Marcar anúncio antigo: `AnuncioAppTaxista_0_excluido = 1`
3. Novo slot idx `1`: preencher `url_imagem`, `url_anuncio` (opcional), multiselect centrais
4. Upload + save

**Remover:**

- Marcar excluído ou desativar radio Não + save

**Particularidade:** só **1** banner ativo; substituir = excluir idx 0 e criar idx 1.

### 7.2 Anúncio passageiro (tela inicial)

**Criar:**

1. Ativar: `AnuncioTelaInicialAppPass_exibir_anuncio_0`
2. Achar slot vazio (0–2) ou chamar lógica equivalente a `adicionarNovoAnuncio`
3. **`link_anuncio` obrigatório**
4. Upload + centrais + save
5. Retornar **`dom_slot_idx`** (sufixo DOM) para remoção futura

**Remover:**

- Por índice: `deletarAnuncio(ord, 'tela_inicial_app_passageiro')` equivalente no form
- Todos: remover cada slot ou marcar excluídos

**Particularidade:** até **3** banners simultâneos.

### 7.3 Campanha ciclo da corrida

**Criar:**

1. Ativar: `Campanha_exibir_campanha_0`
2. Slot `Campanha_{idx}_*`:
   - `url_imagem` ← `urlS3`
   - `url_campanha` (opcional)
   - `limite_solicitacoes_finalizadas` (inteiro)
   - `data_hora_inicio`, `data_hora_fim` (`YYYY-MM-DD`)
   - multiselect `filtro_bandeiras_campanha_{idx}`
3. Upload (`tipo=campanha`) + save

**Remover:**

- Desativar (`Campanha_exibir_campanha_1`) + apagar campanhas, **ou**
- `deletarCampanha(indice)` por slot

**Particularidade:** independente dos 3 slots de tela inicial; exige `limite_corridas` e período.

---

## 8. API REST sugerida para expor

Contrato que **seu produto** consome (implementação interna de vocês):

### 8.1 Autenticação

```http
POST /machine/login
Content-Type: application/json

{
  "email": "operador@bandeira.com",
  "senha": "********",
  "chave_secreta": "VU3EDQM4TG7..." 
}
```

Resposta:

```json
{
  "sucesso": true,
  "session_token": "a1b2c3d4-...",
  "bandeiras": [{"id": "1437", "fuso_horario": "America/Sao_Paulo"}],
  "mensagem": "Login HTTP concluído."
}
```

Alias no repo de referência: `POST /notificacao/login` ou `POST /dinamica/login` (mesma implementação).

### 8.2 Banners

Todas aceitam `session_token` + imagem (`url` ou `base64`):

| Método | Rota sugerida | Equivalente painel |
|--------|---------------|-------------------|
| POST | `/banner/motorista` | Anúncio tela inicial motorista |
| DELETE | `/banner/motorista` | Remove anúncio motorista |
| POST | `/banner/passageiro` | Anúncio tela inicial passageiro |
| DELETE | `/banner/passageiro` | Remove por `indice` ou todos |
| POST | `/banner/corrida` | Campanha ciclo da corrida |
| DELETE | `/banner/corrida` | Remove/desativa campanha |

**Exemplo — campanha corrida:**

```json
POST /banner/corrida
{
  "session_token": "a1b2c3d4-...",
  "bandeira_id": "1437",
  "imagem_base64": "...",
  "link_campanha": "https://promo.exemplo.com",
  "selecionar_todas": true,
  "limite_corridas": 1000,
  "data_inicio": "2026-08-07",
  "data_fim": "2026-09-07"
}
```

Resposta sugerida:

```json
{
  "sucesso": true,
  "mensagem": "Campanha gravada.",
  "dom_idx": 0,
  "url_imagem": "https://s3.../banner.jpeg",
  "verificacao": { "salvo": true, "validado": true }
}
```

### 8.3 Utilitários

| Rota | Função |
|------|--------|
| `GET /health` | Health check |
| `GET /machine/chaves` | Lista emails com TOTP salvo (sem revelar segredo) |
| `POST /machine/codigo` | Gera TOTP atual (debug) |
| `POST /machine/autenticar-acao` | Só o passo `autenticarUsuario2FA` |

---

## 9. Implementação passo a passo (Python)

### Passo 1 — Copiar login HTTP

Use como base `machine_notificacao_http.py`:

- `login_painel()`
- `autenticar_acao_2fa()`
- Pool `_sessions` com TTL

Teste:

```bash
curl -sS -X POST "http://localhost:8000/notificacao/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"SEU_EMAIL","senha":"SUA_SENHA","chave_secreta":"SUA_CHAVE_TOTP"}'
```

### Passo 2 — Criar `machine_bandeira_http.py`

Funções mínimas:

```python
def carregar_form_bandeira(http: requests.Session) -> dict:
    """GET /bandeira/update → bandeiraId, action URL, hidden fields, estado atual."""

def upload_imagem(http, bandeira_id, arquivo_bytes, tipo, campo) -> str:
    """POST salvarImagemConfiguracao → urlS3."""

def salvar_bandeira(http, form_data: dict, codigo_2fa_acao=None) -> dict:
    """POST /bandeira/update; se pedir 2FA, autenticar e repetir."""

def criar_anuncio_motorista(http, bandeira_id, url_imagem, link, bandeiras_ids): ...
def criar_anuncio_passageiro(http, bandeira_id, url_imagem, link, bandeiras_ids): ...
def criar_campanha_corrida(http, bandeira_id, url_imagem, link, limite, data_ini, data_fim, bandeiras_ids): ...
```

**Dica:** inspecionar o HTML de `/bandeira/update` com DevTools → aba Network ao clicar Gravar → copiar o POST exato e replicar field names.

### Passo 3 — Expor rotas FastAPI

Registrar em `api_server.py` espelhando a tabela da [seção 8](#8-api-rest-sugerida-para-expor).

### Passo 4 — Testes manuais

1. Login → token  
2. Criar banner passageiro com imagem pequena  
3. GET painel humano → confirmar visualmente  
4. Remover via API  
5. Repetir para motorista e corrida  

---

## 10. Persistência e segurança

| Dado | Sensibilidade | Recomendação |
|------|---------------|--------------|
| `senha` | Alta | Não logar; não persistir em plain text |
| `chave_secreta` TOTP | **Crítica** | Cofre / volume cifrado; nunca commitar no Git |
| `session_token` | Média | TTL curto; invalidar no logout |
| `PHPSESSID` | Alta | Só em memória do servidor |

**Checklist produção:**

- [ ] HTTPS na API pública  
- [ ] Volume persistente para TOTP  
- [ ] Timeout ≥ 300 s no load balancer  
- [ ] Conta Machine com permissão `/bandeira/update`  
- [ ] Rotação de senha com atualização no cofre  
- [ ] Logs sem senha/TOTP/código 6 dígitos  

---

## 11. Troubleshooting

| Sintoma | Causa provável | Solução |
|---------|----------------|---------|
| `cadastrar2FA: true` | 2FA nunca configurado | Setup manual ou script único |
| `Conta exige código 2FA` sem chave | Segredo não salvo | Enviar `chave_secreta` ou gravar em `chaves_totp.json` |
| HTTP 403 em `/bandeira/update` | Conta sem perfil bandeira | Usar usuário admin da central |
| Upload OK, save falha | Campos obrigatórios faltando | Passageiro: link; corrida: limite + datas |
| Save pede autenticação | 2FA de ação | `autenticarUsuario2FA` + retry |
| `session_token inválido` | TTL 30 min | Login novamente |
| Imagem não aparece no app | Centrais não selecionadas | Multiselect `filtro_bandeiras_*` |
| Código TOTP inválido | Relógio do servidor desalinhado | Sync NTP; gerar código imediatamente antes do POST |

---

## 12. Selenium vs HTTP — quando usar cada um

| Abordagem | Quando usar |
|-----------|-------------|
| **HTTP (este guia)** | Produção, escala, n8n, edge functions, múltiplas cidades |
| **Selenium (legado)** | Prototipação, contas sem 2FA configurado, debug visual |

No repo de referência, rotas `/anuncio-*` e `/banner-corrida` **ainda usam Selenium** (`auto_2fa.py`), mas o **login HTTP e TOTP já funcionam** em `/notificacao/login`. O caminho de migração é implementar `machine_bandeira_http.py` seguindo este documento.

---

## Anexo — Mapa rápido repo de referência

| Arquivo | Conteúdo |
|---------|----------|
| `machine_notificacao_http.py` | Login + 2FA HTTP + notificação |
| `machine_dinamica_http.py` | Tarifa dinâmica (mesma sessão) |
| `auto_2fa.py` | Fluxo Selenium + **referência de campos DOM** |
| `api_server.py` | Rotas FastAPI expostas |
| `API_ENDPOINTS.md` | Contrato Swagger das rotas atuais |
| `docs/banners/REFERENCIA_CAMPOS_PAINEL.md` | IDs e uploads por recurso |

---

**Contato interno Radar / dúvidas de implementação:** adaptem este guia ao stack de vocês; a Machine não documenta oficialmente esses endpoints — mantenham testes de regressão após updates do painel (`bandeira.js`, `campanha.js`).
