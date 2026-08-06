# Lovable — Criar área com polígono

## Onde está no Radar

| Componente | Arquivo |
|------------|---------|
| Botão "Criar área" | `src/Dinamica/pagina_dinamica.tsx` |
| Modal + mapa TerraDraw | `src/Dinamica/modal_criar_area.tsx` |
| Hook | `src/Dinamica/hook_criar_area.ts` |
| Edge function | `supabase/functions/criar-area-dinamica/index.ts` |

---

## Como funciona hoje (automação)

```
ModalCriarArea.salvar()
  → useCriarAreaDinamica.mutateAsync({ cidade_id, nome_area, vertices, fator, ... })
  → invoke("criar-area-dinamica")
  → POST {AUTOMATION_URL}/dinamica/criar-area  (Selenium, até 180s)
  → se ok: INSERT em areas_dinamica com area_id_machine, fator_id_machine
```

Mensagem na UI: *"Criando área no TaxiMachine… pode levar até 3 minutos."*

---

## Substituir por

```
invoke("criar-area-dinamica")  [mesmo body]
  → loginMachine(usuario, senha)
  → POST {DINAMICA_API_URL}/dinamica/areas/criar
  → INSERT areas_dinamica (igual hoje)
```

---

## Passo a passo para o Lovable

### 1. Alterar `criar-area-dinamica/index.ts`

**Remover** chamada a `/dinamica/criar-area` (Selenium).

**Adicionar:**

```typescript
const session_token = await loginMachine(cidade.usuario, cidade.senha);
const bandeira_id = String(cidade.bandeira_machine_id ?? "");

const payload = {
  session_token,
  bandeira_id,
  nome_area: body.nome_area.trim(),
  fator: body.tipo_calculo === "M" ? String(body.fator) : undefined,
  valor_adicional: body.tipo_calculo === "F" ? String(body.valor_adicional) : undefined,
  tipo_calculo: body.tipo_calculo,
  tipo_fator: body.tipo_fator,
  cor_preenchimento: cor,
  vertices: body.vertices.map((v) => ({
    lat: String(v.lat),
    lng: String(v.lng),
  })),
};

const r = await fetch(`${API_BASE}/dinamica/areas/criar`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
  signal: controller.signal,
});
const resp = await r.json();
```

### 2. Mapear resposta

```typescript
const area = resp.area ?? {};
const areaId = area.area_id ?? null;
const fatorId = area.fator_id ?? null;
const okAuto = resp.sucesso === true && areaId;
```

### 3. INSERT Supabase (manter lógica atual)

```typescript
await supabase.from("areas_dinamica").insert({
  ...
  area_id_machine: areaId,
  fator_id_machine: fatorId,
  area_busca_machine: area.nome ?? body.nome_area,
});
```

### 4. Retorno para o front (manter contrato)

```typescript
return json(200, {
  ok: true,
  success: true,
  message: "Área criada com sucesso",
  area_id: areaId,
  fator_id: fatorId,
  area_dinamica_id: inserida?.id,
});
```

### 5. Timeout

Reduzir de **180s → 45s**.

### 6. Remover check `automation_ativo === false`?

**Decisão sugerida:** manter o check — indica se a cidade usa integração Machine. Só trocar a implementação de Selenium → HTTP.

---

## Formato dos vértices

O front envia `{ lat: number, lng: number }[]`.  
A API aceita e converte internamente para `lat,lng;lat,lng;` (formato do painel).

---

## Prompt sugerido para o Lovable

> Na edge function `criar-area-dinamica`, substitua a chamada Selenium `POST /dinamica/criar-area` por login + `POST /dinamica/areas/criar` na API `DINAMICA_API_URL`. Use `bandeira_machine_id` da cidade. Mantenha validações, INSERT em `areas_dinamica` e o formato de resposta `{ ok, success, area_id, fator_id, area_dinamica_id }`. Não altere `modal_criar_area.tsx`.

---

## Teste

Após criar, confirmar no mapa Machine (`mapa_areas_machine` → Buscar) que a área aparece com polígono correto.
