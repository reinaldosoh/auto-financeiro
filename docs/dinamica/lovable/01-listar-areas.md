# Lovable — Listar áreas na Machine (mapa + importação)

## Onde está no Radar

| Componente | Arquivo | Hook / função |
|------------|---------|---------------|
| Mapa Machine | `src/Dinamica/mapa_areas_machine.tsx` | `useAreasMachine` → botão "Buscar áreas" |
| Importar áreas | `src/Dinamica/modal_importar_areas_machine.tsx` | `useAreasMachine` → `lista.refetch()` |
| Modal criar (referência) | `src/Dinamica/modal_criar_area.tsx` | `useAreasMachine` (overlay de existentes) |

**Hook:** `src/Dinamica/hook_areas_machine.ts`  
**Edge function:** `supabase/functions/listar-areas-dinamica/index.ts`

---

## Como funciona hoje (automação)

```
Front → supabase.functions.invoke("listar-areas-dinamica", { cidade_id })
     → Edge lê cidades.usuario/senha
     → POST {AUTOMATION_URL}/dinamica/listar-areas  (Selenium, ~20–120s)
     → { ok, areas[], bandeira_id, global }
```

O front cacheia em `localStorage` (`radar:areas_machine:{cidadeId}`).

---

## Substituir por

```
Front → supabase.functions.invoke("listar-areas-dinamica", { cidade_id })  [mesmo contrato]
     → loginMachine(usuario, senha)
     → GET {DINAMICA_API_URL}/dinamica/areas?session_token=...&bandeira_id=...&incluir_vertices=true
     → mapear resposta para formato AreaMachine[]
```

---

## Passo a passo para o Lovable

### 1. Alterar `listar-areas-dinamica/index.ts`

**Remover:**

```typescript
fetch(`${base}/dinamica/listar-areas`, {
  body: JSON.stringify({ email, password, headless: true }),
});
```

**Adicionar:**

```typescript
import { loginMachine } from "../_shared/dinamica_http.ts";

const session_token = await loginMachine(cidade.usuario, cidade.senha);
const bandeira_id = String(cidade.bandeira_machine_id ?? "");
if (!bandeira_id) {
  return json(400, { ok: false, message: "Cidade sem bandeira_machine_id." });
}

const url = new URL(`${API_BASE}/dinamica/areas`);
url.searchParams.set("session_token", session_token);
url.searchParams.set("bandeira_id", bandeira_id);
url.searchParams.set("incluir_vertices", "true");

const r = await fetch(url.toString(), { signal: controller.signal });
const data = await r.json();
```

### 2. Mapear resposta API → formato atual do front

A API retorna `areas[]` com campos resumidos. Converter para `AreaMachine`:

```typescript
const areas = (data.areas ?? []).map((a: any) => ({
  area_id: String(a.area_id),
  fator_id: String(a.fator_id),
  nome: a.nome,
  fator: String(a.fator ?? ""),
  valor_adicional: String(a.valor_adicional ?? ""),
  tipo_calculo: a.tipo_calculo === "F" ? "F" : "M",
  tipo_fator: a.tipo_fator ?? "P",
  ativo: a.ativo ? "1" : "0",
  cor_preenchimento: a.cor_preenchimento ?? "#ff0000",
  bandeira_id: String(a.bandeira_id ?? bandeira_id),
  vertices: a.vertices ?? [],
  lat_minima: a.lat_minima,
  lat_maxima: a.lat_maxima,
  lng_minima: a.lng_minima,
  lng_maxima: a.lng_maxima,
}));

return json(200, {
  ok: true,
  success: true,
  areas,
  bandeira_id: Number(bandeira_id),
  global: data.global ?? null,
  message: null,
});
```

### 3. Manter contrato do front

**Não alterar** `hook_areas_machine.ts` nem `mapa_areas_machine.tsx` — só a edge function.

### 4. Timeout

Reduzir timeout de **120s → 30s** (HTTP direto é muito mais rápido).

---

## Teste manual

```bash
# 1. Login
curl -s -X POST https://reinaldo-automachine.sw5bxa.easypanel.host/dinamica/login \
  -H "Content-Type: application/json" \
  -d '{"email":"...","password":"..."}'

# 2. Listar
curl -s "https://reinaldo-automachine.sw5bxa.easypanel.host/dinamica/areas?session_token=TOKEN&bandeira_id=1437&incluir_vertices=true"
```

---

## Benefício esperado

- Busca no mapa: de ~20s (Selenium) para **1–3s** (HTTP).
- Menos falhas por timeout no EasyPanel/Render.
