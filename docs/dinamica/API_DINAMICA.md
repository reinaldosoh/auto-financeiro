# API de Tarifa Dinâmica — TaxiMachine (HTTP interno)

Serviço FastAPI (`auto-financeiro`) que espelha os endpoints internos do painel Machine (`cloud.taximachine.com.br/tarifaCategoria/*`), sem Selenium.

**Base URL (EasyPanel):** `https://reinaldo-automachine.sw5bxa.easypanel.host`

---

## Autenticação (obrigatória antes de tudo)

Todas as rotas abaixo exigem `session_token` retornado pelo login.

```http
POST /dinamica/login
Content-Type: application/json

{
  "email": "operador@empresa.com",
  "password": "********"
}
```

**Resposta:**

```json
{
  "sucesso": true,
  "session_token": "abc123...",
  "email": "operador@empresa.com"
}
```

O token é um identificador de sessão HTTP no servidor (cookie `PHPSESSID` do painel). **Não expira automaticamente** enquanto o servidor mantiver a sessão; se receber erro de sessão expirada, faça login novamente.

> Alias: `POST /notificacao/login` usa a mesma sessão.

---

## Visão geral das rotas

| Rota | Método | Equivalente painel Machine |
|------|--------|----------------------------|
| `/dinamica/login` | POST | `POST /site/login` |
| `/dinamica/areas` | GET | `GET /tarifaCategoria/dinamicaArea` |
| `/dinamica/areas/ativar` | POST | `POST /tarifaCategoria/ativarFator` |
| `/dinamica/areas/editar-fator` | POST | `POST /tarifaCategoria/editarFatorDinamica` (só fator) |
| `/dinamica/areas/editar` | POST | `POST /tarifaCategoria/editarFatorDinamica` (completo) |
| `/dinamica/areas/criar` | POST | `POST /tarifaCategoria/criarAreaDinamica` |
| `/dinamica/areas/apagar` | POST | `POST /tarifaCategoria/apagarFatorDinamica` |

---

## 1. Listar áreas

```http
GET /dinamica/areas?session_token=TOKEN&bandeira_id=1437&incluir_vertices=false&apenas_ativas=
```

| Query | Obrigatório | Descrição |
|-------|-------------|-----------|
| `session_token` | sim | Token do login |
| `bandeira_id` | sim | ID da central (`cidades.bandeira_machine_id`) |
| `incluir_vertices` | não | `true` para trazer polígonos (resposta grande) |
| `apenas_ativas` | não | `true` / `false` para filtrar |

**Resposta resumida:**

```json
{
  "sucesso": true,
  "bandeira_id": "1437",
  "total": 194,
  "total_ativas": 110,
  "global": { "fator_id": "...", "fator": "1.0", "ativo": false },
  "areas": [
    {
      "area_id": "577849",
      "fator_id": "56439762",
      "nome": "*Região 02: Centro",
      "ativo": false,
      "fator": "1.20",
      "tipo_calculo": "M",
      "tipo_fator": "P",
      "bandeira_id": "1437"
    }
  ]
}
```

---

## 2. Ativar / desativar área

```http
POST /dinamica/areas/ativar
Content-Type: application/json

{
  "session_token": "TOKEN",
  "bandeira_id": "1437",
  "fator_id": "56439762",
  "area_id": "577849",
  "ativo": true
}
```

**Resposta:** `{ "sucesso": true, "ativo": true, "resposta_bruta": "true" }`

Use o **`fator_id` mais recente** da listagem ou da última edição de fator.

---

## 3. Editar multiplicador (sem alterar polígono)

```http
POST /dinamica/areas/editar-fator
Content-Type: application/json

{
  "session_token": "TOKEN",
  "bandeira_id": "1437",
  "fator_id": "56439762",
  "area_id": "577849",
  "fator": "1.80",
  "tipo_calculo": "M"
}
```

**Resposta:**

```json
{
  "sucesso": true,
  "fator_id_novo": "56439787",
  "area": {
    "area_id": "577849",
    "fator_id": "56439787",
    "fator": "1.80",
    "ativo": true
  }
}
```

### Regra crítica: `fator_id` muda a cada edição

O painel Machine **invalida o fator anterior** e cria um novo registro. Sempre persista `fator_id_novo` no Supabase (`areas_dinamica.fator_id_machine`) após editar.

Erro típico com ID antigo:

> *"O Fator selecionado já foi modificado!"*

---

## 4. Criar área com polígono

```http
POST /dinamica/areas/criar
Content-Type: application/json

{
  "session_token": "TOKEN",
  "bandeira_id": "1437",
  "nome_area": "Região Centro - Teste",
  "fator": "1.50",
  "tipo_calculo": "M",
  "tipo_fator": "P",
  "cor_preenchimento": "#ff0000",
  "vertices": [
    { "lat": "-19.501000", "lng": "-42.601000" },
    { "lat": "-19.501100", "lng": "-42.601000" },
    { "lat": "-19.501100", "lng": "-42.600900" },
    { "lat": "-19.501000", "lng": "-42.600900" }
  ]
}
```

**Validações (painel):**
- Multiplicador (`tipo_calculo: "M"`): entre **1.1** e **5.0**, diferente de 1.0
- Valor adicional (`tipo_calculo: "F"`): entre **0.50** e **5.00**
- Mínimo **3 vértices**
- Polígono sem auto-interseção

**Resposta:**

```json
{
  "sucesso": true,
  "area": {
    "area_id": "665637",
    "fator_id": "56439774",
    "nome": "Região Centro - Teste",
    "fator": "1.50",
    "ativo": true,
    "vertices": [...]
  }
}
```

---

## 5. Editar área completa (nome + polígono + fator)

```http
POST /dinamica/areas/editar
Content-Type: application/json

{
  "session_token": "TOKEN",
  "bandeira_id": "1437",
  "fator_id": "56439774",
  "area_id": "665637",
  "nome_area": "Região Centro - Atualizada",
  "fator": "1.60",
  "tipo_calculo": "M",
  "tipo_fator": "P",
  "cor_preenchimento": "#ffa500",
  "area_alterada": true,
  "vertices": [
    { "lat": "-19.501000", "lng": "-42.601000" },
    ...
  ]
}
```

Retorna `fator_id_novo` se o fator ou geometria mudou.

---

## 6. Apagar área

```http
POST /dinamica/areas/apagar
Content-Type: application/json

{
  "session_token": "TOKEN",
  "bandeira_id": "1437",
  "fator_id": "56439774",
  "area_id": "665637"
}
```

**Resposta:** `{ "sucesso": true, "resposta_bruta": "true" }`

---

## Tipos e constantes

| Campo | Valores | Significado |
|-------|---------|-------------|
| `tipo_calculo` | `"M"` | Multiplicador (ex: 1.5x) |
| `tipo_calculo` | `"F"` | Valor adicional fixo (R$) |
| `tipo_fator` | `"P"` | Embarque (parada) |
| `tipo_fator` | `"R"` | Destino |

---

## Fluxo recomendado (qualquer operação)

```
1. POST /dinamica/login          → session_token
2. Chamar rota desejada          → usar bandeira_id da cidade
3. Se editou fator               → salvar fator_id_novo
4. Se sessão expirou             → repetir passo 1
```

---

## Deploy

Commit `094333d` no GitHub (`reinaldosoh/auto-financeiro`). Após push, **redeploy no EasyPanel** para publicar as novas rotas.

---

## n8n

Workflows importáveis e guia de nós HTTP: [`n8n/N8N_DINAMICA.md`](./n8n/N8N_DINAMICA.md)

---

## Documentação Lovable (migração Selenium → API)

Guias passo a passo por função da tela `/dinamica`:

- [lovable/00-login.md](./lovable/00-login.md)
- [lovable/01-listar-areas.md](./lovable/01-listar-areas.md)
- [lovable/02-importar-areas.md](./lovable/02-importar-areas.md)
- [lovable/03-criar-area.md](./lovable/03-criar-area.md)
- [lovable/04-ativar-desativar-area.md](./lovable/04-ativar-desativar-area.md)
- [lovable/05-editar-fator.md](./lovable/05-editar-fator.md)
- [lovable/06-deletar-area.md](./lovable/06-deletar-area.md)
- [lovable/07-acionar-manual.md](./lovable/07-acionar-manual.md)
- [lovable/08-calcular-automatico.md](./lovable/08-calcular-automatico.md)
