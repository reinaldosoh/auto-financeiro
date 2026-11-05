# Cobrança de royalties (Cenários 1 e 2) via HTTP

Reproduz, sem navegador, o que o financeiro faz à mão no painel da Machine (vídeo "COBRANÇA ROYALTIES - CENÁRIO 1"):

> Pego o mês fechado → **ganhos gerais** (transacionado) → **Configurações › Controle de crédito**: taxa da central 15% (20% só na maçaneta) → 15% × transacionado → menos o **boleto da Machine** → menos o **seguro app** → × **% da Ubiz**.

Código: [`machine_financeiro_http.py`](../../machine_financeiro_http.py) (cliente Machine + cálculo) e [`financeiro_royalties.py`](../../financeiro_royalties.py) (rotas + job + Supabase).

## Rotas Machine usadas

| Passo | Machine |
|---|---|
| Login | `POST /site/login` → `solicitarCodigo2FA` → `POST /site/verificar2FA {code}` |
| Seletor de cidade | `POST /site/trocarCentral {bandeiraId}` · lista em `<script id="sso-centrais-iniciais">` |
| Ganhos gerais | `GET /site/estatistica?tipo=operacao&periodo=mes_passado&bandeira_id=…` |
| Taxas | `GET /bandeira/taxas` (fallback `/bandeira/creditos`), cartões `button.btn-tax` |
| Boleto da Machine | `POST /demonstrativoMensal/index` `FinCobrancaBandeira[bandeira_id]` + `data_ano_mes` → "Valor a pagar" |

## Fórmula (validada com o financeiro: Alfenas 10/2026)

```
saldo_01     = ganhos_gerais × taxa_central%                       126.520,10 × 15% = 18.978,01
boleto_ubiz  = Σ quantidade do Demonstrativo × tabela Ubiz + 3,50   = 3.739,09
seguro_app   = corridas_finalizadas × taxa_seguro (0,15) + 3,50    = 1.239,20
base         = saldo_01 − boleto_ubiz − seguro_app                 = 13.999,72
royalty      = base × % da cidade (20%)                            = 2.799,94
valor_final  = royalty + tarifa de boleto (3,50)                   = 2.803,44
```

### Cenário 2 (taxa da central FIXA por corrida — ex.: Passos, R$ 2,50)

Vídeo "COBRANÇA ROYALTIES - CENÁRIO 2": Corridas › Consultar corridas › filtro do mês, status
Finalizada → quantidade × taxa fixa da central − valor da tecnologia (boleto) → × % da Ubiz.

```
saldo        = corridas_finalizadas × R$ fixo da central
base         = saldo − boleto_ubiz            (sem seguro app; forçar com cfg descontar_seguro=true)
valor_final  = base × % da cidade + tarifa de boleto
```

O cenário é detectado sozinho pela tela de taxas: Taxa da central em **%** → cenário 1;
em **R$** → cenário 2. A quantidade de corridas finalizadas vem de **Consultar corridas**
(dia 1 00:00 ao último dia 23:59, status Finalizada), igual ao financeiro. O Painel pode dar
alguns a menos (Passos 10/2026: Painel 5.096, filtro 5.098 → R$ 1.072,56 conferido).

**O boleto NÃO é o "Valor a pagar" da Machine** (R$ 3.134,97 em Alfenas). A Ubiz usa as
quantidades do demonstrativo com a própria tabela de preços:

| Item do demonstrativo | Tabela Ubiz |
|---|---|
| Corridas finalizadas | 0,33 |
| Corridas não finalizadas (perdidas/canceladas) | 0,17 |
| SMS | 0,14 |
| Validação CPF | 0,14 |
| Rota exibida durante a estimativa | 0,02 |
| Rota exibida durante a viagem | 0,02 |
| Transbordo | 0,25 |
| Saque Pix | 0,50 |
| Tarifa de boleto | 3,50 |

Os preços vêm de `TABELA_UBIZ_PADRAO` (`machine_financeiro_http.py`). Preços maiores que zero em
`empresas.taxa_global_*` no finance-hub (ou `tabela_ubiz` no body) sobrescrevem a tabela. Item sem
preço na tabela usa o valor da Machine e gera aviso. A Taxa App de 20% (só MAÇANETA) não entra
no cenário 01.

## Fluxo da API (todas com `X-API-Key`)

1. **`POST /financeiro/login`** `{email?, senha?}` (padrão: env `FINANCEIRO_MATRIZ_EMAIL` / `FINANCEIRO_MATRIZ_SENHA`)
   - `{"status":"aguardando_2fa","login_id":"…","expira_em_seg":300}` → o front mostra a tela **"Digite o código do autenticador"**.
2. **`POST /financeiro/login/2fa`** `{login_id, codigo}` → `{"status":"autenticado","session_token":"…","centrais":[{bandeira_id,nome,cidade,atual}]}`
   - Código errado: HTTP 400 `codigo_invalido` (até 5 tentativas). Passados 5 min: HTTP 410 `login_expirado`.
3. **`POST /financeiro/royalties/rodar`** (header `X-Session-Token`)
   ```json
   { "ano_mes": "2026-10", "bandeira_ids": ["2796"], "dry_run": true }
   ```
   - Sem `bandeira_ids`, usa todas as cidades do finance-hub com `cenario_01` e `bandeira_id`.
   - Sem Supabase, envie `config_padrao` ou `config_por_bandeira` (`taxa_valor`, `taxa_tipo`, `taxa_seguro_app[_tipo]`, `tarifa_boleto[_tipo]`).
   - `dry_run:false` grava em `cobrancas` (`cenario_01`, e `seguro_app` se a cidade tiver esse cenário) e marca `rodou_mes_atual`. Não duplica o mês, a menos que se passe `sobrescrever:true`.
4. **`GET /financeiro/royalties/status/{job_id}`** (mesmo `X-Session-Token`) → progresso, `totais` e, por cidade, `entradas` (ganhos, taxas, demonstrativo), `calculo` e `avisos`.

As cidades rodam **em sequência**: trocar a central muda o estado da sessão na Machine. No fim, a sessão volta para a central original (matriz). Uma rodada por sessão: uma segunda chamada recebe 409 `rodada_em_andamento`.

## Variáveis de ambiente (VPS / Easypanel)

| Variável | Uso |
|---|---|
| `FINANCE_HUB_SUPABASE_URL` | URL do projeto Supabase do finance-hub |
| `FINANCE_HUB_SUPABASE_SERVICE_KEY` | service role (só no servidor) |
| `FINANCE_HUB_EMPRESA_ID` | opcional: limita às cidades dessa empresa |
| `FINANCEIRO_MATRIZ_EMAIL` / `FINANCEIRO_MATRIZ_SENHA` | opcional: login padrão da matriz |
| `FINANCEIRO_PAUSA_CIDADES_SEG` | pausa entre cidades (padrão 1,5 s) |

Migração do finance-hub: [`finance-hub-migration.sql`](finance-hub-migration.sql).

## Pontos a validar na 1ª rodada real
- Saída de `/bandeira/taxas` (a tela mudou de rota): os cartões devem continuar com `.short_title` / `.taxa-content`.
- Competência diferente do mês passado usa `periodo=personalizado` com datas `dd/mm/aaaa`. Confirmar com a tela (a resposta traz aviso).
- `corridas_finalizadas` do seguro vem do Painel (`origemFinalizada`). O demonstrativo traz outra contagem ("Corridas finalizadas" faturadas), também devolvida em `entradas.demonstrativo`.
