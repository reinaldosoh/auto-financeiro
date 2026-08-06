# Lovable — Cálculo automático + aplicar multiplicador

## Onde está no Radar

| Componente | Arquivo |
|------------|---------|
| Botão "Recalcular" | `src/Dinamica/pagina_dinamica.tsx` |
| Toggle área ativa | `painel_areas_dinamica.tsx` → dispara recalcular |
| Hook | `hook_dinamica.ts` → `useCalcularAgora` |
| Edge function | `supabase/functions/calcular-dinamica/index.ts` |
| Disparo Machine | `_shared/dinamica_automacao.ts` → `dispararAutomacaoDinamica` |

---

## Como funciona hoje

```
useCalcularAgora → invoke("calcular-dinamica?cidade_id=...")
  → Para cada areas_dinamica ativa com config_dinamica:
       1. Calcula conversão/aceitação (eventos_webhook + polígono)
       2. Define multiplicador sugerido (matriz de níveis)
       3. INSERT controle_dinamica
       4. Se multiplicador mudou → dispararAutomacaoDinamica (Selenium)
       5. INSERT historico_dinamica
```

**Cron externo** também chama `calcular-dinamica` periodicamente.

---

## O que mudar vs o que manter

| Parte | Mudar? |
|-------|--------|
| Cálculo estatístico (eventos, polígono, matriz) | **Não** — permanece no Supabase |
| INSERT `controle_dinamica` / `historico_dinamica` | **Não** |
| `dispararAutomacaoDinamica` (Selenium) | **Sim** → API HTTP |

---

## Substituir disparo Selenium por

Ver [05-editar-fator.md](./05-editar-fator.md) — mesma sequência:

```typescript
// Pseudocódigo dentro de calcularArea(), quando mudou && !aquecendo:

const session_token = await loginMachine(cidade.usuario, cidade.senha);
const bandeira_id = String(cidade.bandeira_machine_id);

// 1) Editar fator
const edit = await fetch(`${API_BASE}/dinamica/areas/editar-fator`, {
  body: JSON.stringify({
    session_token,
    bandeira_id,
    fator_id: area.fator_id_machine,
    area_id: area.area_id_machine,
    fator: String(nivelInfo.multiplicador),
    tipo_calculo: area.tipo_calculo ?? "M",
  }),
});
const editResp = await edit.json();
const fatorIdNovo = editResp.fator_id_novo;

// 2) Ativar/desativar
const deveDesativar = nivelInfo.multiplicador <= 1.1;
await fetch(`${API_BASE}/dinamica/areas/ativar`, {
  body: JSON.stringify({
    session_token,
    bandeira_id,
    fator_id: fatorIdNovo ?? area.fator_id_machine,
    area_id: area.area_id_machine,
    ativo: !deveDesativar,
  }),
});

// 3) Persistir fator_id novo
if (fatorIdNovo) {
  await supabase.from("areas_dinamica")
    .update({ fator_id_machine: fatorIdNovo })
    .eq("id", area.id);
}
```

---

## Passo a passo para o Lovable

1. Refatorar **`_shared/dinamica_automacao.ts`** (compartilhado com acionar-manual).
2. **`calcular-dinamica/index.ts`**: trocar import — sem mudar lógica de cálculo.
3. Garantir `bandeira_machine_id` preenchido em `cidades` para todas as cidades com automação.
4. Garantir `area_id_machine` + `fator_id_machine` em áreas importadas/criadas.
5. Remover variável `AUTOMATION_URL` quando migração completa (ou manter só como fallback temporário).

---

## Estados que NÃO disparam Machine (manter)

| Condição | Comportamento |
|----------|---------------|
| `aquecendo` (janela não fechou) | Não dispara |
| `!demandaSuficiente` | Mult = 1.0, pode disparar reset |
| `!amostraMaduraSuficiente` | Reset para piso |
| `!cidade.automation_ativo` | Ignora área |
| `multiplicador` não mudou | Não dispara |

---

## Performance esperada

| Antes | Depois |
|-------|--------|
| 30–120s por área (Selenium) | 2–5s por área (HTTP) |
| Timeouts frequentes | Raro |

Com 10+ áreas por cidade, ganho acumulado é grande.

---

## Prompt sugerido para o Lovable

> Em `calcular-dinamica`, substitua `dispararAutomacaoDinamica` (Selenium) pela implementação HTTP em `_shared/dinamica_automacao.ts` usando `DINAMICA_API_URL`. Não altere a lógica de cálculo estatístico nem as tabelas `controle_dinamica`/`historico_dinamica`. Após editar fator, atualize `areas_dinamica.fator_id_machine`. Não altere `pagina_dinamica.tsx`.

---

## Funções da tela que NÃO usam automação Machine

Estas **não** entram nesta migração:

- Webhooks de status (`/dinamica/webhooks`, `taximachine-webhook-manager`)
- Configuração de regra por área (drawer — só Supabase)
- Cards de engajamento / histórico (leitura Supabase)
- Desligar webhooks TM (`useDesligarWebhookStatus`)
