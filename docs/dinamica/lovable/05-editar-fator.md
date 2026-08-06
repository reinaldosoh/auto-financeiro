# Lovable — Editar multiplicador / valor adicional

## Onde está no Radar

| Componente | Arquivo | Função |
|------------|---------|--------|
| "Salvar valor" no mapa | `src/Dinamica/mapa_areas_machine.tsx` | `onSalvarEdicao()` |
| Acionar automático | `_shared/dinamica_automacao.ts` | `dispararAutomacaoDinamica` → `acao: "editar"` |
| Edge gerenciar | `gerenciar-area-dinamica` | `acao: "editar"` |

---

## Como funciona hoje

### Mapa (UI manual)

```
onSalvarEdicao()
  → gerenciar.mutateAsync({ acao: "editar", fator_id, area_id, fator|valor_adicional })
  → POST {AUTOMATION_URL}/dinamica/area/acao
  → areas.refetch()  // fator_id muda após editar
```

### Automação (calcular-dinamica + acionar manual)

`_shared/dinamica_automacao.ts`:

1. `POST /dinamica/area/acao` com `acao: "editar"` + multiplicador
2. `POST /dinamica/area/acao` com `acao: "ativar"` ou `"desativar"` (se mult ≤ 1.1 desativa)

Fallback legado sem IDs: `POST /dinamica/atualizar` (busca área por nome no DOM — **remover**).

---

## Substituir por

### Editar fator (mapa + automação)

```http
POST /dinamica/areas/editar-fator
{
  "session_token": "...",
  "bandeira_id": "1437",
  "fator_id": "56439762",
  "area_id": "577849",
  "fator": "1.80",
  "tipo_calculo": "M"
}
```

**Resposta inclui `fator_id_novo`** — obrigatório persistir.

### Valor adicional (`tipo_calculo: "F"`)

```json
{
  "tipo_calculo": "F",
  "valor_adicional": "2.50",
  "fator": null
}
```

(API trata `tipo_calculo !== "M"` enviando `valor_adicional`.)

---

## Passo a passo para o Lovable

### 1. `gerenciar-area-dinamica` — ramo `editar`

```typescript
const session_token = await loginMachine(cidade.usuario, cidade.senha);

const payload: Record<string, unknown> = {
  session_token,
  bandeira_id: String(bandeira_id),
  fator_id: body.fator_id,
  area_id: body.area_id,
  tipo_calculo: body.tipo_calculo ?? "M",
};

if (body.tipo_calculo === "F") {
  payload.valor_adicional = String(body.valor_adicional);
} else {
  payload.fator = String(body.fator);
}

const r = await fetch(`${API_BASE}/dinamica/areas/editar-fator`, { method: "POST", ... });
const resp = await r.json();

return json(200, {
  ok: resp.sucesso,
  success: resp.sucesso,
  acao: "editar",
  data: {
    ...resp.area,
    fator_id: resp.fator_id_novo ?? resp.area?.fator_id,
  },
});
```

### 2. `_shared/dinamica_automacao.ts` — reescrever `dispararAutomacaoDinamica`

**Fluxo novo (com IDs):**

```typescript
// 1) Editar fator
await fetch(`${API_BASE}/dinamica/areas/editar-fator`, { ... });
// Guardar fator_id_novo — atualizar areas_dinamica.fator_id_machine se possível

// 2) Toggle ativo (mesma lógica: mult <= 1.1 → desativar)
await fetch(`${API_BASE}/dinamica/areas/ativar`, {
  body: JSON.stringify({ ..., ativo: multiplicador > 1.1 }),
});
```

**Remover:**
- `chamarAtualizarLegado` (`/dinamica/atualizar` Selenium)
- Dependência de `area_busca_machine` quando IDs existem

**Manter fallback** só se `fator_id_machine` e `area_id_machine` ausentes (log + erro claro pedindo reimportar área).

### 3. Atualizar `fator_id_machine` no Supabase após editar

Após `editar-fator` bem-sucedido:

```typescript
if (areaDinamicaId && resp.fator_id_novo) {
  await supabase.from("areas_dinamica")
    .update({ fator_id_machine: resp.fator_id_novo })
    .eq("id", areaDinamicaId);
}
```

Isso evita falhas nas próximas ativações/automações.

---

## Regra crítica

| Evento | Comportamento Machine |
|--------|----------------------|
| Editar fator | Gera **novo** `fator_id` |
| Usar ID antigo | Erro "Fator já foi modificado/excluído" |

---

## Prompt sugerido para o Lovable

> Substitua em `gerenciar-area-dinamica` (ação editar) e em `_shared/dinamica_automacao.ts` as chamadas Selenium por login + `POST /dinamica/areas/editar-fator` seguido de `POST /dinamica/areas/ativar`. Persista `fator_id_novo` em `areas_dinamica.fator_id_machine`. Remova o fallback `/dinamica/atualizar` quando a área tiver `area_id_machine` e `fator_id_machine`.
