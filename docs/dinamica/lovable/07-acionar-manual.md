# Lovable — Acionar multiplicador manualmente

## Onde está no Radar

| Componente | Arquivo |
|------------|---------|
| Botão "Acionar" por área | `src/Dinamica/painel_areas_dinamica.tsx` |
| Modal | `src/Dinamica/modal_acionar_multiplicador.tsx` |
| Hook | `hook_dinamica.ts` → `useAcionarManual` |
| Edge function | `supabase/functions/dinamica-acionar-manual/index.ts` |

---

## Como funciona hoje

```
ModalAcionarMultiplicador.disparar(teste?)
  → invoke("dinamica-acionar-manual", {
       cidade_id,
       area_dinamica_id,
       multiplicador,
       teste
     })
  → Valida permissões + credenciais
  → dispararAutomacaoDinamica(cidade, areaRow, multiplicador)  // Selenium
  → INSERT historico_dinamica
```

Mensagem UI: *"Automação em execução… pode levar até 2 minutos."*

A automação Selenium (`dinamica_automacao.ts`):
1. Edita fator na Machine
2. Ativa/desativa toggle (mult ≤ 1.1 → desliga)

---

## Substituir por

Mesma sequência via API HTTP (ver [05-editar-fator.md](./05-editar-fator.md)):

1. `loginMachine`
2. `POST /dinamica/areas/editar-fator` com o multiplicador informado
3. `POST /dinamica/areas/ativar` com `ativo: multiplicador > 1.1`
4. Atualizar `fator_id_machine` se retornou `fator_id_novo`
5. INSERT `historico_dinamica` (manter)

---

## Passo a passo para o Lovable

### 1. Alterar `dinamica-acionar-manual/index.ts`

**Substituir** import/call de `dispararAutomacaoDinamica` por versão HTTP em `_shared/dinamica_automacao.ts` (refatorada).

### 2. Pré-requisitos da área

Continuar exigindo (já existe):

- `area_id_machine` + `fator_id_machine` **ou** `area_busca_machine`
- `cidade.usuario` + `cidade.senha`
- `automation_ativo === true`

Com API HTTP, **IDs são obrigatórios** — remover fallback Selenium por nome.

Se faltar ID:

```json
{
  "ok": false,
  "mensagem": "Reimporte a área do TaxiMachine para obter area_id e fator_id atualizados."
}
```

### 3. Modo teste (`teste: true`)

Manter flag no histórico (`origem: "teste"`).  
Opcional: no modo teste, **não** chamar Machine — só simular (decisão de produto). Hoje o teste **executa de verdade** no Selenium.

### 4. Timeout

Reduzir de **120s → 30s**.

### 5. Resposta para o front (manter)

```typescript
return json(200, {
  ok: okAutomacao,
  resposta: respostaApi,
  erro: erroAutomacao,
  mensagem: erroAutomacao,
});
```

---

## Campos Supabase usados

| Tabela | Campo |
|--------|-------|
| `areas_dinamica` | `area_id_machine`, `fator_id_machine`, `area_busca_machine`, `multiplicador_minimo`, `tipo_calculo` |
| `historico_dinamica` | registro do disparo manual |

---

## Prompt sugerido para o Lovable

> Refatore `dinamica-acionar-manual` e `_shared/dinamica_automacao.ts` para usar `DINAMICA_API_URL` (login + editar-fator + ativar) em vez de Selenium. Exija `area_id_machine` e `fator_id_machine`. Atualize `fator_id_machine` após editar. Mantenha insert em `historico_dinamica` e contrato `{ ok, resposta, erro }`. Não altere `modal_acionar_multiplicador.tsx`.
