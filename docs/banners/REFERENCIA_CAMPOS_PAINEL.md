# Referência — Campos do painel (Recursos Premium)

Página base: **`https://cloud.taximachine.com.br/bandeira/update`** → aba **Recursos premium**.

Formulário único: `#bandeira-form` · Botão salvar: `#btn-salvar-bandeira` · Variável JS: `bandeiraId`.

---

## 1. Anúncio — app motorista (tela inicial)

| Item | Valor |
|------|--------|
| **Onde aparece no app** | Tela inicial do app **motorista** |
| **TIPO (JS)** | `tela_inicial_app_taxista` |
| **Prefixo do formulário** | `AnuncioAppTaxista` |
| **Ativar recurso** | Radio Sim: `AnuncioAppTaxista_exibir_anuncio_0` |
| **Desativar** | Radio Não: `AnuncioAppTaxista_exibir_anuncio_1` |
| **Slots** | 1 anúncio ativo por vez (estratégia: marcar idx `0` como excluído e criar idx `1`) |
| **Link** | Opcional · `AnuncioAppTaxista_{idx}_url_anuncio` |
| **Imagem (URL S3)** | `AnuncioAppTaxista_{idx}_url_imagem` |
| **Excluído** | `AnuncioAppTaxista_{idx}_excluido` (`1` = removido) |
| **Centrais (multiselect)** | `filtro_bandeiras_anuncio_tela_inicial_app_taxista_{idx}` |
| **Upload XHR** | `tipo=anuncio`, `campo=anuncio` |

**Funções JS úteis:** `alteraVisibilidadeCamposAnuncios('tela_inicial_app_taxista')`, `adicionarNovoAnuncio('tela_inicial_app_taxista')`, `obterListaAnuncio('tela_inicial_app_taxista')`.

---

## 2. Anúncio — app passageiro (tela inicial)

| Item | Valor |
|------|--------|
| **Onde aparece no app** | Tela inicial do app **passageiro** |
| **TIPO (JS)** | `tela_inicial_app_passageiro` |
| **Prefixo do formulário** | `AnuncioTelaInicialAppPass` |
| **Variável JS da lista** | `listaAnunciosTelaInicialAppPassageiro` |
| **Ativar recurso** | `AnuncioTelaInicialAppPass[exibir_anuncio]` → `""` + `"1"` (hidden + radio Sim) |
| **Slots** | Até **70** anúncios (`MAP_ANUNCIO_TIPO_MAX[passageiro] = 70`) |
| **Índice do slot** | Posição no array JS: existentes `0..N-1`; **novo = `len(lista)`** após `lista.push({})` |
| **Link** | **Obrigatório no slot novo** · `AnuncioTelaInicialAppPass[lista][idx][url_anuncio]` |
| **Imagem** | `AnuncioTelaInicialAppPass[lista][idx][url_imagem]` |
| **ID (existente)** | `AnuncioTelaInicialAppPass[lista][idx][id]` — omitir ou vazio no INSERT |
| **Excluído** | `AnuncioTelaInicialAppPass[lista][idx][excluido]` (`0` ativo, `1` removido) |
| **Ativo** | `AnuncioTelaInicialAppPass[lista][idx][ativo]` = `1` |
| **Centrais** | `AnuncioTelaInicialAppPass[lista][idx][bandeiras][]` (multiselect, vários valores) |
| **Upload XHR** | `tipo=anuncio`, `campo=anuncio` |

### Não existe API separada para “+ Adicionar novo anúncio”

O botão só roda JS no browser (`adicionarNovoAnuncio`):

1. `copiarDadosAnuncios(tipo)` — lê DOM → array JS  
2. `lista.push({})` — novo item vazio  
3. `exibirRecursoPremiumAnuncio(tipo)` — re-renderiza inputs `[lista][idx]`

**Gravar** = único POST HTTP:

```http
POST https://cloud.taximachine.com.br/bandeira/update
Referer: https://cloud.taximachine.com.br/bandeira/update
Content-Type: application/x-www-form-urlencoded
```

Body inclui **todo** `#bandeira-form` (~295 campos `Bandeira[...]`) + campos dinâmicos dos anúncios + `yt1=Gravar`.

### Exemplo — 3 anúncios existentes + criar o 4º (índice 3)

Ordem típica após upload da imagem:

```
AnuncioTelaInicialAppPass[exibir_anuncio]=
AnuncioTelaInicialAppPass[exibir_anuncio]=1

AnuncioTelaInicialAppPass[lista][0][url_imagem]=https://asset-cnt.../anuncio_6038235153.jpg
AnuncioTelaInicialAppPass[lista][0][id]=3525
AnuncioTelaInicialAppPass[lista][0][excluido]=0
AnuncioTelaInicialAppPass[lista][0][ativo]=1
AnuncioTelaInicialAppPass[lista][0][bandeiras][]=3615
AnuncioTelaInicialAppPass[lista][0][bandeiras][]=5272
AnuncioTelaInicialAppPass[lista][0][bandeiras][]=5575
# url_anuncio OMITIDO — permite_alterar_url_anuncio=false (campo disabled no painel)

... slots 1 e 2 idem (sem url_anuncio se bloqueado) ...

AnuncioTelaInicialAppPass[lista][3][url_imagem]=https://asset-cnt.../tmp/...jpg
AnuncioTelaInicialAppPass[lista][3][url_anuncio]=https://seu-link.com/
AnuncioTelaInicialAppPass[lista][3][excluido]=0
AnuncioTelaInicialAppPass[lista][3][ativo]=1
AnuncioTelaInicialAppPass[lista][3][bandeiras][]=2014
# id omitido no INSERT

yt1=Gravar
```

**Regras importantes para HTTP puro:**

- **Índice** = `len(listaAnunciosTelaInicialAppPassageiro)` no GET anterior (ex.: 3 ads → novo slot `[lista][3]`).
- **Não enviar** `bandeira_id` nem `tipo_anuncio` — não existem no HTML gerado por `obterHTMLAnuncio`.
- **Não enviar** `url_anuncio` em slots com `permite_alterar_url_anuncio: false` (browser não serializa disabled).
- Upload antes: `POST /bandeira/salvarImagemConfiguracao` → usar `urlS3` em `[url_imagem]`.
- Se pedir 2FA: `POST /site/autenticarUsuario2FA` + repetir o save (Referer `/bandeira/update`).

**Funções JS:** `adicionarNovoAnuncio`, `copiarDadosAnuncios`, `exibirRecursoPremiumAnuncio`, `obterHTMLAnuncio`, `deletarAnuncio`.

---

## 3. Campanha — ciclo da corrida (app passageiro)

| Item | Valor |
|------|--------|
| **Onde aparece no app** | **Durante toda a corrida** (busca → destino) |
| **Prefixo do formulário** | `Campanha` |
| **Ativar recurso** | `Campanha_exibir_campanha_0` (Sim) / `_1` (Não) |
| **Slots** | Lista dinâmica · linhas `#campanha-{idx}` |
| **Link** | Opcional · `Campanha_{idx}_url_campanha` |
| **Imagem** | `Campanha_{idx}_url_imagem` |
| **Limite de corridas** | `Campanha_{idx}_limite_solicitacoes_finalizadas` |
| **Período início** | `Campanha_{idx}_data_hora_inicio` (`YYYY-MM-DD`) |
| **Período fim** | `Campanha_{idx}_data_hora_fim` (`YYYY-MM-DD`) |
| **Centrais** | `filtro_bandeiras_campanha_{idx}` |
| **Upload XHR** | `tipo=campanha`, `campo=campanha` |
| **Excluído** | `Campanha_{idx}_excluido` |

**Funções JS:** `alteraVisibilidadeCamposCampanhas()`, `exibirRecursoPremiumCampanha()`, `adicionarNovaCampanha()`, `copiarDadosCampanhas()`, `deletarCampanha(idx)`, `apagarTodasCampanhas()`.

**Imagem recomendada:** 640×480 px, horizontal, PNG/JPG, máx. 2 MB.

**Preço Machine (referência):** ~R$ 0,02 × `limite_corridas` (teto estimado).

---

## 4. Upload de imagem (comum aos três)

```http
POST https://cloud.taximachine.com.br/bandeira/salvarImagemConfiguracao
Content-Type: multipart/form-data
X-Requested-With: XMLHttpRequest
Cookie: PHPSESSID=...
Referer: https://cloud.taximachine.com.br/bandeira/update
```

| Campo multipart | Valor |
|-----------------|--------|
| `foto` | arquivo binário (JPEG/PNG) |
| `id` | `bandeiraId` (ID da central no painel) |
| `tipo` | `anuncio` ou `campanha` |
| `campo` | `anuncio` ou `campanha` |

**Resposta JSON (sucesso):**

```json
{
  "success": true,
  "urlS3": "https://...",
  "fotoName": "banner.jpeg"
}
```

Use `urlS3` no campo `*_url_imagem` ou `Campanha_*_url_imagem` antes de gravar o formulário.

---

## 5. Gravar alterações

Equivalente ao clique em **Gravar** (`#btn-salvar-bandeira`):

1. Montar POST com todos os campos de `#bandeira-form` (incluindo hidden fields obtidos no GET inicial).
2. Incluir o botão de submit no body (nome varia; inspecionar o HTML da página).
3. Aceitar que o painel pode exibir alert JS (“Deseja salvar?”) — em HTTP isso não existe; o POST deve ir direto.
4. Se a resposta indicar **autenticação necessária**, chamar `POST /site/autenticarUsuario2FA` e repetir o save.

**Dica de implementação:** faça `GET /bandeira/update`, parseie o HTML (ou use regex como em `machine_notificacao_http.py`), altere só os campos do recurso desejado e reenvie o form completo via `POST /bandeira/update`.
