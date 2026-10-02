# API — Endpoints (TaxiMachine Automação)

Substitua `BASE_URL` pela URL pública do serviço (ex.: `https://seu-dominio.com` ou Easypanel).

Documentação interativa: **`GET {BASE_URL}/docs`** (Swagger UI).

**Guia para outro time (banners via HTTP + 2FA, sem Selenium):** [`docs/banners/GUIA_INTEGRACAO_BANNERS_HTTP.md`](docs/banners/GUIA_INTEGRACAO_BANNERS_HTTP.md)

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

### `BannerCorridaInput` (`/banner-corrida`)

| Campo              | Obrigatório | Default   | Descrição                                      |
|--------------------|-------------|-----------|------------------------------------------------|
| `email`            | sim         | —         | Login                                          |
| `senha`            | sim         | —         | Senha                                          |
| `chave_secreta`    | não         | `null`    | TOTP; omitir se já salvo após `/autenticar`    |
| `headless`         | não         | `true`    |                                                |
| `manter_aberto`    | não         | `false`   |                                                |
| `imagem_url`       | condicional | `null`    | URL pública da imagem (640×480, PNG/JPG)       |
| `imagem_base64`    | condicional | `null`    | Imagem em Base64                               |
| `link_campanha`    | não         | `""`      | URL opcional da campanha                       |
| `selecionar_todas` | não         | `true`    | Seleciona todas as centrais no multiselect     |
| `limite_corridas`  | não         | `1000`    | «Em quantas corridas mostrar a campanha?»      |
| `data_inicio`      | não         | hoje      | `YYYY-MM-DD` (início do período)               |
| `data_fim`         | não         | hoje+30d  | `YYYY-MM-DD` (fim do período)                  |

É obrigatório informar **`imagem_url` ou `imagem_base64`**. A automação **sempre clica em Gravar** ao final.

### `RemoverAnuncioInput` (`/remover-anuncio`, `/remover-anuncio-passageiro`, `/remover-banner-corrida`)

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
| POST | `/banner-corrida` | Campanha no ciclo da corrida (app passageiro) |
| POST | `/remover-banner-corrida` | Remove/desativa campanha no ciclo da corrida |
| POST | `/notificacao/login` | Login HTTP no painel (cookie, **sem Selenium**) |
| GET | `/notificacao/categorias` | Categorias para filtros de notificação em massa |
| POST | `/notificacao/categorias` | Igual ao GET; aceita credenciais ou `session_token` |
| POST | `/relatorio/clientes/solicitar` | Exporta a base de clientes do painel (CSV no S3) |
| GET | `/relatorio/clientes/status/{report_id}` | Status da exportação; `url` do CSV quando pronto |
| POST | `/relatorio/corridas/solicitar` | Exporta corridas dos últimos 90 dias (3 janelas até ontem) em job de background |
| GET | `/relatorio/corridas/status/{job_id}` | Status do job; 1 `url` de CSV por janela |

---

## Exportação de clientes (HTTP direto — sem Selenium)

Replica o botão **Exportar** de [`/cliente/index`](https://cloud.taximachine.com.br/cliente/index). O painel gera o arquivo em background (tempo varia por cidade; Mariana ~47 mil clientes ≈ 20 s) e devolve um link S3 pré-assinado.

- Login, solicitação e polling **precisam rodar na VPS**: o `PHPSESSID` fica preso ao IP de quem logou.
- O CSV **não passa pela VPS**: quem chama baixa direto da `url` (válida ~1 h, sem cookie).
- Formato: separador `;`, encoding latin-1, 28 colunas (`Nome;Telefone;E-mail;Gênero;...;ID;Qtd. corridas finalizadas;...;Versão do aplicativo`) — o mesmo aceito pela importação RC.

### `POST /relatorio/clientes/solicitar`

| Campo           | Obrigatório | Descrição |
|-----------------|-------------|-----------|
| `session_token` | condicional | De `/notificacao/login` |
| `email`/`senha` | condicional | Login automático se não enviar `session_token` |
| `codigo_2fa` / `chave_secreta` | não | Contas com 2FA (a chave salva no servidor é usada se omitir) |
| `filtros`       | não | Objeto com qualquer um de: `nome`, `telefone`, `email`, `cpf`, `status_cliente` (`"null"` = todos), `tipo_cliente`, `bandeira_configuracao_id`, `incluir_cliente_com_cartao_nao_validado`, `criado_em_ini`, `criado_em_fim`, `data_nascimento_ini`, `data_nascimento_fim` (datas `dd/mm/aaaa`). Vazio = base inteira |
| `aguardar_seg`  | não | `0` (default) devolve só o `report_id`; `>0` espera o CSV ficar pronto (máx. 240) |

**Resposta:**

```json
{
  "sucesso": true,
  "session_token": "uuid...",
  "report_id": 4739424,
  "status_export": "ready",
  "pronto": true,
  "cancelado": false,
  "url": "https://cloud-machine-global.s3.amazonaws.com/reports/report4739424.csv?X-Amz-...",
  "url_expira_em": 1790946000
}
```

### `GET /relatorio/clientes/status/{report_id}?session_token=...`

`status_export`: `generate` → `processing` → `ready` (ou `canceled`). `pronto=true` só quando há `url`. Consulte a cada ~10 s.

**Exemplo — uma chamada, espera até 2 min e baixa o CSV:**

```bash
URL=$(curl -sS -X POST "$BASE/relatorio/clientes/solicitar" \
  -H "Content-Type: application/json" \
  -d '{"email":"conta@exemplo.com","senha":"SUA_SENHA","aguardar_seg":120}' \
  --max-time 300 | python3 -c "import sys,json; print(json.load(sys.stdin)['url'] or '')")

curl -sS -o clientes.csv "$URL"
```

**Exemplo — cidades grandes (sem prender a requisição):**

```bash
R=$(curl -sS -X POST "$BASE/relatorio/clientes/solicitar" -H "Content-Type: application/json" \
  -d '{"email":"conta@exemplo.com","senha":"SUA_SENHA"}')
TOKEN=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_token'])")
ID=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['report_id'])")

curl -sS "$BASE/relatorio/clientes/status/$ID?session_token=$TOKEN"   # repetir até pronto=true
```

---

## Exportação de corridas — últimos 90 dias (HTTP direto — sem Selenium)

Replica **Filtro → Exportar relatório** de [`/solicitacao/historicoCorridas2`](https://cloud.taximachine.com.br/solicitacao/historicoCorridas2?resetSesion=1). O painel aceita no máximo **31 dias por filtro**, então a API divide os 90 dias em janelas mensais e roda uma por vez (o filtro fica na sessão PHP).

**Regra das janelas** (fuso `America/Sao_Paulo`):

- O dia atual está incompleto: a última janela termina **ontem 23:59**. Toda janela vai de `00:00` a `23:59`.
- Janela *k*: início = hoje − (*k*+1) meses; fim = (hoje − *k* meses) − 1 dia. Contíguas, sem buraco nem sobreposição.
- Rodando em **02/10/2026**: `02/09–01/10`, `02/08–01/09`, `02/07–01/08`.
- Dia inexistente no mês cai no último dia (ex.: hoje 31/03 → `28/02–30/03`).

Por janela: `relatorioCorridas` (filtro) → `statusRelatorioCorridas` até `status=ready` → `exportarRelatorioCorridas` → `statusRelatorioCorridas` até `statusExport=ready` + `url`. Cada janela leva ~1–2 min (Mariana: 3 janelas em ~4 min, ~36 mil corridas cada).

- Formato: separador `;`, latin-1, 95 colunas (`Nº OS;...;Status;...;Momento da solicitação;...;Telefone do passageiro;...;E-mail de cadastro;...`) — o mesmo aceito pela importação RC.
- Login e polling **rodam na VPS** (cookie preso ao IP); o CSV é baixado direto da `url` S3 (válida ~1 h — baixe logo após a janela ficar `pronta`).
- **Radar:** chamar com as **credenciais da cidade** (`cidades.usuario` / `senha` / `automation_totp`, via `resolverCredenciaisCidadeDashboard`), **não** as de `/credencial-machine`.

### `POST /relatorio/corridas/solicitar`

| Campo           | Obrigatório | Descrição |
|-----------------|-------------|-----------|
| `session_token` | condicional | De `/notificacao/login` |
| `email`/`senha` | condicional | Login automático se não enviar `session_token` |
| `codigo_2fa` / `chave_secreta` | não | Contas com 2FA (a chave salva no servidor é usada se omitir) |
| `data_referencia` | não | `aaaa-mm-dd` que faz papel de "hoje" (testes). Default: hoje em São Paulo |
| `qtd_janelas`   | não | Default `3` (90 dias). Entre 1 e 6 |

Responde **na hora** com o `job_id`. Se já houver job em andamento para o mesmo login, devolve esse job (`reaproveitado: true`) em vez de abrir outro — o painel só guarda um filtro por sessão.

### `GET /relatorio/corridas/status/{job_id}?session_token=...`

Use o `session_token` devolvido no POST (401 se não bater; 404 se o job expirou — jobs ficam 2 h em memória). Consulte a cada ~15 s.

- `status`: `processing` → `ready` (todas as janelas com `url`) ou `erro` (campo `erro` + janela com `etapa: "erro"`).
- `janelas[].etapa`: `aguardando` → `filtrando` → `exportando` → `pronta`.
- Timeout de 5 min por janela.

**Resposta (pronto):**

```json
{
  "sucesso": true,
  "job_id": "uuid...",
  "status": "ready",
  "erro": null,
  "criado_em": 1790950200,
  "atualizado_em": 1790950425,
  "janelas": [
    {"inicio": "2026-09-02", "fim": "2026-10-01", "etapa": "pronta", "report_id": 9876543,
     "url": "https://cloud-machine-global.s3.amazonaws.com/reports/report9876543.csv?X-Amz-...",
     "url_expira_em": 1790953829, "erro": null},
    {"inicio": "2026-08-02", "fim": "2026-09-01", "etapa": "pronta", "...": "..."},
    {"inicio": "2026-07-02", "fim": "2026-08-01", "etapa": "pronta", "...": "..."}
  ],
  "urls": ["https://...report9876543.csv?...", "https://...", "https://..."]
}
```

**Exemplo:**

```bash
R=$(curl -sS -X POST "$BASE/relatorio/corridas/solicitar" -H "Content-Type: application/json" \
  -d '{"email":"conta@exemplo.com","senha":"SUA_SENHA"}')
JOB=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
TOKEN=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_token'])")

curl -sS "$BASE/relatorio/corridas/status/$JOB?session_token=$TOKEN"   # repetir até status=ready
```

---

## Notificação em massa (HTTP direto — sem Selenium)

Rotas que replicam o painel [`/notificacao/create`](https://cloud.taximachine.com.br/notificacao/create).  
**Não** usam a API `integracao/v1/notificacao/push`.

### `POST /notificacao/login`

| Campo           | Obrigatório | Descrição |
|-----------------|-------------|-----------|
| `email`         | sim         | Login TaxiMachine |
| `senha`         | sim         | Senha |
| `codigo_2fa`    | não         | Código TOTP de 6 dígitos (contas com 2FA) |
| `chave_secreta` | não         | Segredo TOTP; gera código automaticamente se omitir `codigo_2fa` |

**Resposta de sucesso:**

```json
{
  "sucesso": true,
  "email": "Dinamica@mariana.com",
  "session_token": "uuid...",
  "phpsessid": "...",
  "bandeiras": [{"id": "3085", "fuso_horario": "America/Sao_Paulo"}],
  "mensagem": "Login HTTP no painel concluído."
}
```

O `session_token` fica em memória no servidor por **~30 minutos**.

### `GET /notificacao/categorias`

Query params:

| Param            | Obrigatório | Descrição |
|------------------|-------------|-----------|
| `session_token`  | sim         | Retornado por `/notificacao/login` |
| `bandeira_id`    | não         | ID da central; omitir usa todas da conta |

### `POST /notificacao/categorias`

Use **`session_token`** **ou** **`email` + `senha`** (login automático).

| Campo           | Obrigatório | Descrição |
|-----------------|-------------|-----------|
| `session_token` | condicional | Token de sessão |
| `email`         | condicional | Com `senha`, se não enviar token |
| `senha`         | condicional | Com `email` |
| `bandeira_id`   | não         | ID da central |
| `codigo_2fa`    | não         | Para contas com 2FA no login automático |
| `chave_secreta` | não         | TOTP alternativo |

**Exemplo — login + categorias:**

```bash
BASE="http://127.0.0.1:8000"

TOKEN=$(curl -sS -X POST "$BASE/notificacao/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"Dinamica@mariana.com","senha":"SUA_SENHA"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['session_token'])")

curl -sS "$BASE/notificacao/categorias?session_token=$TOKEN&bandeira_id=3085"
```

**Exemplo — categorias em uma chamada (email + senha):**

```bash
curl -sS -X POST "$BASE/notificacao/categorias" \
  -H "Content-Type: application/json" \
  -d '{"email":"Dinamica@mariana.com","senha":"SUA_SENHA","bandeira_id":"3085"}'
```

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

Até **3** anúncios no painel: a automação preenche o **primeiro slot vazio** ou acrescenta uma linha com “+ novo” **sem** apagar os anúncios já existentes. Em `verificacao`, use **`dom_slot_idx`** para a remoção posterior.

**Resposta 200:** `ResultadoOutput` (pode incluir `verificacao`).

**Timeout recomendado:** 300–600 s.

---

## POST `/banner-corrida`

Corpo: **`BannerCorridaInput`**

Recurso pago **«Adicionar campanha no ciclo da corrida no app passageiro»** (Recursos Premium). A automação:

1. Marca **Sim** no toggle da campanha;
2. Faz upload da imagem (`tipo=campanha`, endpoint `/bandeira/salvarImagemConfiguracao`);
3. Preenche link (opcional), centrais, limite de corridas e período;
4. Clica em **Gravar** e valida persistência após recarregar o painel.

**Resposta 200:** `ResultadoOutput` (pode incluir `verificacao.preenchimento`, `verificacao.salvo`, `verificacao.validado`).

**Exemplo**

```bash
curl -sS -X POST "${BASE_URL}/banner-corrida" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "conta@exemplo.com",
    "senha": "***",
    "imagem_url": "https://exemplo.com/banner.jpg",
    "link_campanha": "https://exemplo.com/promo",
    "limite_corridas": 1000,
    "headless": true
  }' \
  --max-time 600
```

**Timeout recomendado:** 300–600 s.

---

## POST `/remover-banner-corrida`

Corpo: **`RemoverAnuncioInput`**

Remove campanha no ciclo da corrida (Recursos Premium):

- **`indice` omitido ou `null`:** marca **Não** no toggle, apaga todas as campanhas e **Grava**.
- **`indice` inteiro (0-based):** remove só a campanha na posição informada (botão excluir + **Gravar**).

**Resposta 200:** `ResultadoOutput`

**Exemplo (desativar tudo)**

```bash
curl -sS -X POST "${BASE_URL}/remover-banner-corrida" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "conta@exemplo.com",
    "senha": "***",
    "headless": true,
    "manter_aberto": false
  }' \
  --max-time 600
```

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
