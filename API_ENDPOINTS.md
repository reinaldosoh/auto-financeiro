# API — Endpoints (TaxiMachine Automação)

Substitua `BASE_URL` pela URL pública do serviço (ex.: `https://seu-dominio.com` ou Easypanel).

Documentação interativa: **`GET {BASE_URL}/docs`** (Swagger UI).

---

## Primeiro acesso (TOTP)

1. Chame **`POST /autenticar`** só com `email` e `senha`. A automação conclui o 2FA quando necessário, grava o segredo em `chaves_totp.json` no servidor e pode devolver `chave_totp` no JSON de resposta.
2. Nas demais rotas, `chave_secreta` é **opcional** se a chave já estiver salva no servidor.
3. Em produção, use **volume persistente** para o arquivo `chaves_totp.json` (evita perder a chave a cada redeploy).

---

## Modelos comuns

### `ResultadoOutput` (várias rotas POST)

| Campo         | Tipo    | Descrição                                      |
|---------------|---------|------------------------------------------------|
| `sucesso`     | boolean | Se a operação terminou com sucesso             |
| `email`       | string  | Email da conta                                 |
| `chave_totp`  | string  | Segredo TOTP (Base32), quando aplicável        |
| `mensagem`    | string  | Detalhe ou mensagem de erro                    |
| `verificacao` | object  | Opcional (ex.: checagens pós-anúncio passageiro) |

Em caso de falha de negócio, muitas rotas respondem **HTTP 400** com `detail` espelhando esse formato.

### `CredenciaisInput`

| Campo            | Obrigatório | Default  | Descrição                          |
|------------------|-------------|----------|------------------------------------|
| `email`          | sim         | —        | Login TaxiMachine                  |
| `senha`          | sim         | —        | Senha                              |
| `headless`       | não         | `false`  | Chrome sem interface gráfica       |
| `manter_aberto`  | não         | `true`   | Manter sessão do browser aberta  |

### `AnuncioMotoristaInput` (motorista e passageiro)

| Campo              | Obrigatório | Default   | Descrição                                      |
|--------------------|-------------|-----------|------------------------------------------------|
| `email`            | sim         | —         | Login                                          |
| `senha`            | sim         | —         | Senha                                          |
| `chave_secreta`    | não         | `null`    | TOTP; omitir se já salvo após `/autenticar`    |
| `headless`         | não         | `true`    |                                                |
| `manter_aberto`    | não         | `false`   |                                                |
| `imagem_url`       | condicional | `null`    | URL pública da imagem                          |
| `imagem_base64`    | condicional | `null`    | Imagem em Base64 (pode incluir prefixo `data:`)|
| `link_anuncio`     | passageiro: sim; motorista: não | `""` | URL do anúncio (**obrigatório** em `/anuncio-passageiro`) |
| `selecionar_todas` | não         | `true`    | Centrais / “selecionar todas” no painel        |

É obrigatório informar **`imagem_url` ou `imagem_base64`** nas rotas de anúncio.

### `RemoverAnuncioInput` (`/remover-anuncio` e `/remover-anuncio-passageiro`)

| Campo            | Obrigatório | Default   | Descrição                                                |
|------------------|-------------|-----------|----------------------------------------------------------|
| `email`          | sim         | —         | Login                                                    |
| `senha`          | sim         | —         | Senha                                                    |
| `chave_secreta`  | não         | `null`    | TOTP; omitir se já salvo no servidor                    |
| `headless`       | não         | `true`    |                                                          |
| `manter_aberto`  | não         | `false`   |                                                          |
| `indice`         | não         | `null`    | **Só passageiro:** use `verificacao.dom_slot_idx` do disparo (sufixo DOM) **ou** posição 0-based na lista. Omitir = remover **todos** |

A API aceita strings para `indice`, `headless` e `manter_aberto` (ex.: N8N) e converte quando possível.

---

## Lista de endpoints

| Método | Caminho | Descrição resumida |
|--------|---------|--------------------|
| GET | `/` | Metadados do serviço + links úteis |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger (OpenAPI) |
| POST | `/autenticar` | Setup/login 2FA; grava TOTP no servidor |
| POST | `/autenticar/lote` | Várias contas em sequência |
| POST | `/login` | Login com TOTP já salvo no servidor |
| GET | `/chaves` | Lista emails com chave TOTP salva (sem revelar segredos) |
| POST | `/codigo` | Gera código TOTP de 6 dígitos para email com chave salva |
| POST | `/recursos-premium` | Login e navega até Recursos Premium |
| POST | `/anuncio-motorista` | Cadastra anúncio (app motorista) |
| POST | `/remover-anuncio` | Remove anúncio do motorista |
| POST | `/anuncio-passageiro` | Cadastra anúncio (app passageiro) |
| POST | `/remover-anuncio-passageiro` | Remove um (por `indice`) ou todos os anúncios passageiro |

---

## GET `/`

**Resposta 200**

```json
{
  "ok": true,
  "service": "taximachine-automacao",
  "health": "/health",
  "docs": "/docs"
}
```

---

## GET `/health`

**Resposta 200**

```json
{ "status": "ok" }
```

---

## POST `/autenticar`

Corpo: **`CredenciaisInput`**

**Resposta 200:** `ResultadoOutput`

- Em fluxo de **configuração inicial do 2FA**, `chave_totp` pode vir preenchida e é persistida no servidor.
- Se o login não pedir 2FA, `chave_totp` pode ficar vazio.

**Exemplo**

```bash
curl -sS -X POST "${BASE_URL}/autenticar" \
  -H "Content-Type: application/json" \
  -d '{"email":"conta@exemplo.com","senha":"***","headless":true,"manter_aberto":false}'
```

---

## POST `/autenticar/lote`

Corpo: **array de `CredenciaisInput`**

**Resposta 200:** array de `ResultadoOutput` (uma entrada por credencial, processadas em sequência).

---

## POST `/login`

Corpo: **`CredenciaisInput`**

Login assumindo **TOTP já salvo** no servidor para o email. **Resposta 200:** `ResultadoOutput`

---

## GET `/chaves`

**Resposta 200**

```json
{
  "total": 2,
  "contas": ["email1@exemplo.com", "email2@exemplo.com"]
}
```

Não expõe o segredo TOTP, apenas quais emails têm chave armazenada.

---

## POST `/codigo`

Corpo:

```json
{ "email": "conta@exemplo.com" }
```

**Resposta 200 (sucesso)**

```json
{ "sucesso": true, "codigo": "123456", "email": "conta@exemplo.com" }
```

**Resposta 200 (sem chave)**

```json
{ "sucesso": false, "codigo": "", "mensagem": "Chave TOTP não encontrada para este email." }
```

---

## POST `/recursos-premium`

Corpo: **`CredenciaisInput`**

Login (incluindo 2FA quando necessário, usando chave no servidor) e navegação até **Recursos Premium**.

**Resposta 200:** objeto no formato `ResultadoOutput` (campos alinhados ao retorno interno).

**Erro 400:** `detail` com o resultado da automação.

---

## POST `/anuncio-motorista`

Corpo: **`AnuncioMotoristaInput`** (`imagem_url` **ou** `imagem_base64` obrigatório)

**Resposta 200:** `ResultadoOutput`

**Erro 400:** validação (ex.: falta de imagem) ou falha da automação (`detail`).

**Timeout recomendado no cliente:** 300–600 s.

---

## POST `/remover-anuncio`

Corpo: **`RemoverAnuncioInput`**

Remove o anúncio ativo na seção do **app motorista**. O campo `indice` do modelo **não se aplica** a esta rota (é ignorado na lógica de motorista).

**Resposta 200:** `ResultadoOutput`

---

## POST `/anuncio-passageiro`

Corpo: **`AnuncioMotoristaInput`** com:

- `imagem_url` ou `imagem_base64` obrigatório;
- **`link_anuncio` obrigatório** (texto não vazio).

**Resposta 200:** `ResultadoOutput` (pode incluir `verificacao`).

**Timeout recomendado:** 300–600 s.

---

## POST `/remover-anuncio-passageiro`

Corpo: **`RemoverAnuncioInput`**

- **`indice` omitido ou `null`:** remove **todos** os anúncios de passageiro.
- **`indice` inteiro:** preferir o valor **`dom_slot_idx`** devolvido em `verificacao` no POST `/anuncio-passageiro` (é o sufixo da linha `anuncio-tela_inicial_app_passageiro-N` no painel). Se não existir linha com esse sufixo, o fluxo interpreta `indice` como posição **0-based** na lista ordenada de linhas. Em caso de timeout do painel para o índice pedido, tenta-se **fallback índice 0**.

**Resposta 200:** `ResultadoOutput`

**Timeout recomendado:** 300–600 s.

**Exemplo (remover só o 2.º anúncio)**

```bash
curl -sS -X POST "${BASE_URL}/remover-anuncio-passageiro" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "conta@exemplo.com",
    "senha": "***",
    "headless": true,
    "manter_aberto": false,
    "indice": 1
  }' \
  --max-time 600
```

---

## Erros HTTP

| Código | Quando |
|--------|--------|
| **400** | Validação FastAPI ou falha retornada pela automação (`detail` costuma ser objeto com `sucesso`, `mensagem`, etc.) |
| **422** | Corpo JSON inválido ou campos incompatíveis com o modelo (quando o validador rejeita antes da thread) |
| **500** | Erro interno não tratado na API |

---

## Execução local

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
```

Variável **`PORT`** costuma ser usada em Docker/Easypanel para o Uvicorn escutar na porta correta.

---

## Limite de concorrência

O servidor usa um **`ThreadPoolExecutor` com 3 workers** para as tarefas pesadas (Selenium). Muitas requisições simultâneas podem enfileirar.
