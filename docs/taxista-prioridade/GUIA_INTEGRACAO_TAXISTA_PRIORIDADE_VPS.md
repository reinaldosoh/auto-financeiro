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

## Listar motoristas por relação

`GET /taxista/listar` (headers `X-API-Key` e `X-Session-Token`) — só leitura, lê a grade do painel.

| Parâmetro | Valores | Padrão |
|---|---|---|
| `relacao` | `prioritario`, `secundario`, `todos` | `todos` |
| `status` | letras do painel separadas por vírgula: A ativo, I inativo, S suspenso, G, R rejeitado, D deletado, E em análise, F fila | `A` |
| `pagina` | 1.. (a grade tem 10 por página) | 1 |
| `todas` | `true` percorre todas as páginas (lento em praças grandes; limite de 400 páginas) | `false` |

```bash
curl -sS "$BASE_URL/taxista/listar?relacao=secundario&status=A&todas=true" \
  -H "X-API-Key: $API_KEY" -H "X-Session-Token: $TOKEN"
```

Resposta: `total`, `pagina`, `paginas`, `itens` (`taxista_id`, `nome`, `status`, `categorias`) e `completo`.
O status é sempre enviado ao painel; sem ele o painel usa um filtro salvo na sessão e o total muda.

## Alterar a relação de todos (laço no cliente)

A VPS não tem serviço de laço: o sistema que chama faz o laço. `PATCH /taxista/{taxista_id}/relacao`
altera um motorista por chamada; é idempotente (já no valor pedido → `inalterado: true`, nada é gravado).

```bash
# 1) IDs dos motoristas (ex.: todos os ativos hoje prioritários)
curl -sS "$BASE_URL/taxista/listar?relacao=prioritario&status=A&todas=true" \
  -H "X-API-Key: $API_KEY" -H "X-Session-Token: $TOKEN" | jq -r '.itens[].taxista_id' > ids.txt

# 2) Um PATCH por ID, em série (relacao: prioritario | secundario)
while read -r ID; do
  curl -sS -X PATCH "$BASE_URL/taxista/$ID/relacao" \
    -H "X-API-Key: $API_KEY" -H "X-Session-Token: $TOKEN" -H "Content-Type: application/json" \
    -d '{"relacao":"secundario"}'
  sleep 1
done < ids.txt
```

Regras do laço:
- Uma chamada por vez, reaproveitando o mesmo `X-Session-Token` (não faça login a cada ID).
- Teste antes com `"dry_run": true` e com poucos IDs; confira que `alteracoes_inesperadas` vem vazio.
- Guarde o resultado de cada ID (`inalterado`, sucesso ou `codigo` do erro) para retomar de onde parou. Em erro `sessao_expirada` (401), faça novo login e continue.
- Ignore motoristas deletados (`status=D`): o painel não grava edição neles.

## Corpo opcional

- `"dry_run": true` — lê o formulário e devolve `alteracoes_previstas`, sem gravar e sem pedir 2FA.

## Resposta

- `inalterado: true` — o motorista já estava no valor pedido; nada foi gravado.
- `alteracoes` — diff completo (todos os campos) entre o formulário antes e depois da gravação.
- `alteracoes_inesperadas` — campos alterados além de `Taxista[taxista_apoio]`. Deve vir vazio; se não vier, avise.
- No `dry_run`: `alteracoes_previstas`, `campos_enviados` e `campos_desabilitados_nao_enviados`.
- Semântica de `Taxista[taxista_apoio]`: `0` = prioritário, `1` = secundário.
- A gravação posta o formulário como o navegador: campos desabilitados não são enviados e telefone/vínculo não são alterados.

## Erros

Corpo: `{"sucesso": false, "codigo": "...", "mensagem": "..."}`.

| codigo | HTTP | Significado |
|---|---|---|
| `sessao_expirada` | 401 | Faça login de novo |
| `sem_permissao` | 403 | Conta sem permissão de editar motorista |
| `campo_desabilitado` | 403 | Campo de relação desabilitado para esta conta/motorista |
| `bandeira_desativada` | 403 | Bandeira da praça desativada |
| `usuario_bloqueado` | 403 | Usuário bloqueado no painel |
| `taxista_nao_encontrado` | 404 | Motorista inexistente |
| `painel_nao_gravou` | 409 | O painel respondeu, mas o valor não persistiu |
| `2fa_sem_segredo` | 409 | Falta o segredo 2FA da praça no cofre (`POST /autenticar`) |
| `saldo_minimo` | 422 | O painel recusou o bloco de saldo mínimo |
| `validacao_painel` | 422 | Outra validação do painel |
| `painel_indisponivel` | 502 | Falha de rede com o painel |
| `erro_painel` | 400 | Outro erro |

Timeout recomendado: **180 s** (2FA de ação).
