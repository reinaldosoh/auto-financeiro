# Integração — prioridade do motorista (VPS)

Altera o campo **Relação** no cadastro do taxista no painel Machine.

| Ação no painel | Endpoint VPS |
|----------------|----------------|
| Motorista **prioritário** | `POST /taxista/prioridade/ativar` |
| Motorista **secundário** (remove prioridade) | `POST /taxista/prioridade/remover` |
| Qualquer uma (explícito) | `POST /taxista/relacao` + `"relacao": "prioritario"` ou `"secundario"` |

**OpenAPI:** [`openapi.yaml`](./openapi.yaml)

## Autenticação

1. Header **`X-API-Key`** em todas as chamadas (valor em `MACHINE_API_KEY` no Easypanel).
2. **`POST /notificacao/login`** → guardar `session_token` → header **`X-Session-Token`** nas rotas de taxista.
3. Não coloque `session_token` no JSON do body.

## Corpo mínimo (com sessão)

```json
{
  "taxista_id": 772770
}
```

`taxista_id` = ID numérico do motorista (URL `/taxista/update/{id}`).

## Exemplo cURL

```bash
export BASE_URL="https://sua-vps.exemplo.com"
export API_KEY="..."
export TOKEN="..."   # session_token do login

curl -sS -X POST "$BASE_URL/taxista/prioridade/ativar" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Session-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"taxista_id":772770}'

curl -sS -X POST "$BASE_URL/taxista/prioridade/remover" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Session-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"taxista_id":772770}'
```

Resposta de sucesso inclui `relacao`, `campo`, `valor_anterior`, `valor_aplicado`.

## Erros comuns

- **401** — API key errada ou `session_token` no body em vez do header.
- **400** — telefone inválido no cadastro, taxista inexistente, ou painel rejeitou o POST.
- **429** — muitos logins; reutilize `X-Session-Token`.

Timeout recomendado: **180 s** (2FA de ação).
