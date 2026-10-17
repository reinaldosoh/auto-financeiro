# Integração — API VPS (categorias de tarifa)

Documento para o time que vai consumir a **API de automação** (VPS) no sistema de vocês.

| Item | Valor |
|------|--------|
| **OpenAPI** | [`openapi.yaml`](./openapi.yaml) |
| **Swagger UI** | Abra [`swagger.html`](./swagger.html) no navegador (veja abaixo) |
| **Código de referência** | [github.com/reinaldosoh/auto-financeiro](https://github.com/reinaldosoh/auto-financeiro) |

Substitua `BASE_URL` pela URL que o operador da VPS informar (ex.: Easypanel).

---

## 1. O que a API faz

| Método | Caminho | Função |
|--------|---------|--------|
| `GET` | `/health` | Teste de conectividade |
| `POST` | `/notificacao/login` | Login no painel Machine → `session_token` |
| `POST` | `/categoria/listar` | Lista categorias (id, nome, ordem, exibir no app) |
| `PATCH` | `/categoria/ordem` | Define posição por **nome** (`posicao` ≥ 1) |
| `PATCH` | `/categoria/exibir` | Liga/desliga exibição no **app passageiro** |

No painel Machine isso corresponde a **Categorias → Tarifa** e à edição de cada categoria (ordem e toggle de exibição).

---

## 2. Autenticação

### 2.1 Chave da VPS (obrigatória)

Todas as requisições:

```http
X-API-Key: <segredo fornecido pelo operador>
Content-Type: application/json
```

Sem essa chave a API responde **401**.

**Onde está a chave (operador da VPS, não o integrador):**

| Onde | Variável |
|------|----------|
| Easypanel → serviço **auto-financeiro** → **Environment** | `MACHINE_API_KEY` |

Não commitar no Git. Crie um valor aleatório com **pelo menos 32 caracteres**, salve no Easypanel, redeploy, e envie ao integrador por canal seguro.

Exemplo de geração no Mac: `openssl rand -base64 48`

### 2.2 Painel Machine (cidade)

Conta de **login da cidade** no painel (e-mail, senha e TOTP se habilitado).

**Recomendado:** uma vez por sessão (~30 min):

1. `POST {BASE_URL}/notificacao/login` com `email`, `senha`, `chave_secreta` (TOTP Base32).
2. Guardar `session_token` da resposta.
3. Nas rotas `/categoria/*`, enviar:

```http
X-Session-Token: <session_token>
```

Corpo mínimo em listar: `{}`.

**Importante:** não envie `session_token` no JSON — a API exige o **header** `X-Session-Token`.

Alternativa (evitar em loop): enviar `email` + `senha` (+ `chave_secreta`) no body de cada rota. Isso dispara login a cada chamada e pode gerar **429** (rate limit).

### 2.3 2FA em gravações

Alterar ordem ou exibição pode acionar 2FA de **ação** no painel. Mantenha `chave_secreta` disponível na primeira chamada sem sessão ou configure TOTP no servidor VPS via `POST /autenticar` (operador).

---

## 3. Swagger interativo

1. Copie a pasta `docs/categorias-tarifa/` (contém `openapi.yaml` + `swagger.html`).
2. Sirva por HTTP local (necessário para o YAML carregar):

```bash
cd docs/categorias-tarifa
python3 -m http.server 8765
```

3. Abra `http://127.0.0.1:8765/swagger.html`
4. Clique em **Authorize** e informe a `X-API-Key`.
5. Em **Servers**, ajuste a URL base da VPS.
6. Use **Try it out** — para `/categoria/*`, preencha também o header `X-Session-Token` após o login.

Também é possível importar `openapi.yaml` no [Swagger Editor](https://editor.swagger.io/) ou no Postman.

---

## 4. Exemplos cURL

```bash
export BASE_URL="https://sua-vps.exemplo.com"
export API_KEY="sua-chave-api"
export EMAIL="cidade@exemplo.com"
export SENHA="..."
export TOTP="JBSWY3DPEHPK3PXP"
```

**Health**

```bash
curl -sS "$BASE_URL/health" -H "X-API-Key: $API_KEY"
```

**Login**

```bash
TOKEN=$(curl -sS -X POST "$BASE_URL/notificacao/login" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"senha\":\"$SENHA\",\"chave_secreta\":\"$TOTP\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_token',''))")
echo "session=$TOKEN"
```

**Listar**

```bash
curl -sS -X POST "$BASE_URL/categoria/listar" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Session-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Ordem — Maçaneta na posição 1**

```bash
curl -sS -X PATCH "$BASE_URL/categoria/ordem" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Session-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"nome":"Maçaneta","posicao":1}'
```

**Exibir no app passageiro**

```bash
curl -sS -X PATCH "$BASE_URL/categoria/exibir" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Session-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"nome":"Maçaneta","exibir":true}'
```

---

## 5. Erros comuns

| HTTP | Significado |
|------|-------------|
| 401 | `X-API-Key` inválida ou `session_token` no body em vez do header |
| 400 | Nome de categoria não encontrado, posição inválida, validação do painel |
| 429 | Muitos logins com e-mail/senha — aguarde e use `X-Session-Token` |
| 502/timeout | Operação pesada; timeout do cliente ≥ 120 s em ordem/exibir |

Corpo de erro típico:

```json
{
  "detail": {
    "sucesso": false,
    "mensagem": "Texto legível"
  }
}
```

---

## 6. Contato operacional

- URL base e `X-API-Key`: operador da VPS.
- Credenciais do painel Machine: responsável pela cidade/bandeira.
- Dúvidas de comportamento no painel: suporte TaxiMachine / documentação interna da central.
