# Lovable — Importar áreas do TaxiMachine para o Radar

## Onde está no Radar

| Componente | Arquivo |
|------------|---------|
| Botão "Importar do TaxiMachine" | `src/Dinamica/painel_areas_dinamica.tsx` |
| Modal de seleção | `src/Dinamica/modal_importar_areas_machine.tsx` |
| Persistência Supabase | `hook_areas_dinamica_regra.ts` → `useImportarAreasMachine` |

---

## Como funciona hoje

1. Modal abre → `useAreasMachine.refetch()` → edge **`listar-areas-dinamica`** (Selenium).
2. Usuário marca áreas na tabela.
3. `useImportarAreasMachine` grava em `areas_dinamica` (tabela Supabase) — **sem chamar Machine de novo**.

Campos salvos por área:

- `area_id_machine`, `fator_id_machine`, `nome`, `vertices`, `fator`, `tipo_calculo`, etc.
- `area_busca_machine` = nome da área (usado no fallback legado Selenium)
- `origem` = `"machine"`

---

## O que mudar

**Somente o passo 1** (listagem) — ver [01-listar-areas.md](./01-listar-areas.md).

A importação em si (`useImportarAreasMachine`) **permanece igual** — é CRUD no Supabase.

---

## Passo a passo para o Lovable

1. Migrar `listar-areas-dinamica` para API HTTP (documento 01).
2. **Garantir** que o mapeamento inclua `fator_id` e `area_id` corretos — são essenciais para acionar/editar depois sem Selenium.
3. Após importar, validar no Supabase:

```sql
SELECT nome, area_id_machine, fator_id_machine, area_busca_machine
FROM areas_dinamica
WHERE cidade_id = '...';
```

4. Opcional: na importação, atualizar `fator_id_machine` se a listagem trouxer ID mais recente que o cache local.

---

## Prompt sugerido para o Lovable

> Migre a edge function `listar-areas-dinamica` para usar a API HTTP em `DINAMICA_API_URL` em vez de `AUTOMATION_URL/dinamica/listar-areas`. Sempre faça login via `POST /dinamica/login` antes. Use `cidades.bandeira_machine_id` como `bandeira_id`. Mantenha o formato de resposta `{ ok, success, areas, bandeira_id }` que o front já espera. Não altere `modal_importar_areas_machine.tsx` nem `useImportarAreasMachine`.

---

## O que NÃO muda

- UI do modal
- Lógica insert/update em `areas_dinamica`
- Toggle "Ativa/Pausada" da regra (é flag Supabase `ativo_dinamica`, não toggle Machine)
