# Lovable — Login antes de qualquer chamada Machine

## O que existe hoje

Todas as edge functions de dinâmica leem `cidades.usuario` e `cidades.senha` e repassam para o serviço Selenium (`AUTOMATION_URL`):

| Edge function | Uso das credenciais |
|---------------|---------------------|
| `listar-areas-dinamica` | `{ email, password }` → `POST /dinamica/listar-areas` |
| `criar-area-dinamica` | `{ email, password, ... }` → `POST /dinamica/criar-area` |
| `gerenciar-area-dinamica` | `{ email, password, acao, ... }` → `POST /dinamica/area/acao` |
| `_shared/dinamica_automacao.ts` | `{ email, password, ... }` → `POST /dinamica/area/acao` ou `/dinamica/atualizar` |

**Arquivos front:** não fazem login direto — delegam às edge functions.

---

## O que mudar

Substituir o envio de email/senha em **cada** chamada Selenium por um fluxo em **2 passos**:

1. **Login uma vez** → obter `session_token`
2. **Usar `session_token`** em todas as rotas `/dinamica/*`

---

## Nova API

```http
POST https://reinaldo-automachine.sw5bxa.easypanel.host/dinamica/login
Content-Type: application/json

{
  "email": "{{ cidades.usuario }}",
  "password": "{{ cidades.senha }}"
}
```

**Resposta esperada:**

```json
{
  "sucesso": true,
  "session_token": "uuid-ou-hash",
  "email": "operador@empresa.com"
}
```

---

## Passo a passo para o Lovable

1. Criar helper compartilhado (edge function ou módulo `_shared/dinamica_http.ts`):

```typescript
const API_BASE = Deno.env.get("DINAMICA_API_URL")?.replace(/\/+$/, "")
  ?? "https://reinaldo-automachine.sw5bxa.easypanel.host";

export async function loginMachine(email: string, password: string) {
  const r = await fetch(`${API_BASE}/dinamica/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await r.json();
  if (!data?.session_token) throw new Error(data?.detail ?? "Login Machine falhou");
  return data.session_token as string;
}
```

2. Adicionar secret **`DINAMICA_API_URL`** no Supabase (substitui ou complementa `AUTOMATION_URL`).

3. Em **cada** edge function que hoje chama `AUTOMATION_URL`, trocar o início por:

```typescript
const session_token = await loginMachine(cidade.usuario, cidade.senha);
const bandeira_id = String(cidade.bandeira_machine_id);
```

4. **Nunca** enviar senha para rotas que não sejam `/dinamica/login`.

5. Tratar sessão expirada: se qualquer rota retornar erro contendo "Sessão expirada" ou HTTP 401, refazer login e repetir **uma vez**.

---

## Campos Supabase usados

| Tabela | Campo | Uso |
|--------|-------|-----|
| `cidades` | `usuario` | email do painel Machine |
| `cidades` | `senha` | senha do painel Machine |
| `cidades` | `bandeira_machine_id` | `bandeira_id` em todas as rotas |

---

## O que NÃO muda no front

A UI `/dinamica` continua igual. Só as edge functions deixam de chamar Selenium.
