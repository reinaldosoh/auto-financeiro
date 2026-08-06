# Lovable — Ativar / desativar área na Machine

## Onde está no Radar

| Componente | Arquivo | Função |
|------------|---------|--------|
| Switch "Ativa" no mapa | `src/Dinamica/mapa_areas_machine.tsx` | `onToggleAtivo()` |
| Hook | `src/Dinamica/hook_areas_machine.ts` | `useGerenciarArea` |
| Edge function | `supabase/functions/gerenciar-area-dinamica/index.ts` | `acao: "ativar" \| "desativar"` |

---

## Como funciona hoje

```
Switch onCheckedChange
  → gerenciar.mutateAsync({
       cidade_id, acao: "ativar"|"desativar",
       fator_id: areaSel.fator_id,
       area_id: areaSel.area_id
     })
  → POST {AUTOMATION_URL}/dinamica/area/acao  (Selenium)
  → areas.refetch()
```

---

## Substituir por

```
loginMachine()
  → POST /dinamica/areas/ativar
     {
       session_token,
       bandeira_id,
       fator_id,
       area_id,
       ativo: true|false
     }
```

---

## Passo a passo para o Lovable

### 1. Alterar `gerenciar-area-dinamica/index.ts`

Para `acao === "ativar"` ou `"desativar"`:

```typescript
const session_token = await loginMachine(cidade.usuario, cidade.senha);
const bandeira_id = String(
  (await supabase.from("cidades").select("bandeira_machine_id").eq("id", cidade.id).single())
    .data?.bandeira_machine_id ?? ""
);

const r = await fetch(`${API_BASE}/dinamica/areas/ativar`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    session_token,
    bandeira_id,
    fator_id: body.fator_id,
    area_id: body.area_id,
    ativo: body.acao === "ativar",
  }),
});
const resp = await r.json();
const ok = resp.sucesso === true;
```

### 2. Manter resposta para o front

```typescript
return json(200, { ok, success: ok, acao: body.acao, message: ok ? null : resp.detail });
```

### 3. Usar `fator_id` atualizado

Se a área foi editada recentemente, o mapa pode ter `fator_id` desatualizado. Após qualquer `editar-fator`, o front deve atualizar a lista (`areas.refetch()`) — a API de listagem traz o ID novo.

Erro comum com ID velho:

> *"O Fator enviado já foi excluído!"*

**Solução:** sempre refetch após editar fator; opcionalmente persistir `fator_id_machine` no Supabase ao editar.

---

## Prompt sugerido para o Lovable

> Em `gerenciar-area-dinamica`, para ações `ativar` e `desativar`, use login + `POST /dinamica/areas/ativar` em vez de Selenium. Mantenha o contrato `{ ok, success, acao }`. Não altere `mapa_areas_machine.tsx`.

---

## Diferença: toggle Radar vs toggle Machine

| Ação | Onde | O que faz |
|------|------|-----------|
| Switch no **Painel de áreas com regra** | `painel_areas_dinamica.tsx` | `ativo_dinamica` no Supabase (pausa cálculo) |
| Switch no **Mapa Machine** | `mapa_areas_machine.tsx` | Liga/desliga área **no painel TaxiMachine** |

Este documento trata **somente** o switch do mapa Machine.
