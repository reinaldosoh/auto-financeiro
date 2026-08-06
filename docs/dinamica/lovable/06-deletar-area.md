# Lovable — Deletar área na Machine

## Onde está no Radar

| Componente | Arquivo |
|------------|---------|
| Botão "Deletar área" | `src/Dinamica/mapa_areas_machine.tsx` → `onDeletar()` |
| Hook | `useGerenciarArea` em `hook_areas_machine.ts` |
| Edge function | `gerenciar-area-dinamica` → `acao: "deletar"` |

---

## Como funciona hoje

```
AlertDialog confirmar
  → gerenciar.mutateAsync({
       acao: "deletar",
       fator_id, area_id, cidade_id
     })
  → POST {AUTOMATION_URL}/dinamica/area/acao  (Selenium)
  → areas.refetch()
```

**Nota:** excluir regra no Radar (`painel_areas_dinamica` → lixeira) **não** deleta na Machine — só remove registro Supabase.

---

## Substituir por

```http
POST /dinamica/areas/apagar
{
  "session_token": "...",
  "bandeira_id": "1437",
  "fator_id": "56439774",
  "area_id": "665637"
}
```

---

## Passo a passo para o Lovable

### 1. Ramo `deletar` em `gerenciar-area-dinamica`

```typescript
const session_token = await loginMachine(cidade.usuario, cidade.senha);

const r = await fetch(`${API_BASE}/dinamica/areas/apagar`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    session_token,
    bandeira_id: String(bandeira_id),
    fator_id: body.fator_id,
    area_id: body.area_id,
  }),
});
const resp = await r.json();
const ok = resp.sucesso === true;
```

### 2. Resposta para o front (manter)

```typescript
return json(200, { ok, success: ok, acao: "deletar", message: ok ? null : "..." });
```

### 3. Opcional: limpar Supabase

Se existir `areas_dinamica` com mesmo `area_id_machine`, considerar marcar como removida ou apagar vínculo — **hoje o front não faz isso automaticamente** ao deletar no mapa.

---

## Prompt sugerido para o Lovable

> Em `gerenciar-area-dinamica`, ação `deletar`: use login + `POST /dinamica/areas/apagar`. Mantenha contrato de resposta. Não altere `mapa_areas_machine.tsx`.

---

## Cuidado operacional

Deletar na Machine é **irreversível**. O AlertDialog atual já avisa — manter esse UX.
