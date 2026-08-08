# Radar — Credencial Machine (por empresa)

Projeto: [Radar Mobility](https://lovable.dev/projects/31840afa-6b6a-4a25-b1dd-84c3993ef751)

## Dois tipos de credencial (não confundir)

| Tipo | Onde cadastra | 2FA | Para quê |
|------|---------------|-----|----------|
| **Credencial Machine** | Menu **Credencial Machine** (`/credencial-machine`) | **Obrigatório** (automático) | Banners, dinâmica painel, notificações — **todas as cidades** |
| **Credenciais da cidade** | Card da cidade → Credenciais | **Não** | Webhook engajamento, API Key local da bandeira |

## Por que um login único por empresa?

A TaxiMachine exige um **usuário administrador** com permissão sobre **todas as centrais** da operação. Banners e automações do painel não funcionam com um login “só de uma cidade” sem escopo global.

Exemplo UBIZCAR: um login (`radar1@gmail.com`) com acesso a todas as cidades UBIZCAR — cadastrado **uma vez** em **Credencial Machine**.

## O que o operador preenche (Credencial Machine)

- Email do painel  
- Senha  

**Não** preenche: TOTP (sistema registra), URL da API (secret `AUTOMATION_URL` no Supabase).

## Fluxo 2FA automático

```
Salvar email + senha
  → Validar acesso
  → VPS POST /autenticar (1ª vez) ou /notificacao/login
  → Grava machine_painel_totp na empresa (invisível ao usuário)
  → Badge "2FA: configurado automaticamente"
```

## Banco (empresas)

| Coluna | Uso |
|--------|-----|
| `machine_painel_email` | Login painel |
| `machine_painel_senha` | Senha |
| `machine_painel_totp` | TOTP — só edge function |

Disparos de banner leem **empresa** via `cidades.empresa_id`, não credencial por cidade.

## Secrets

| Secret | Valor |
|--------|--------|
| `AUTOMATION_URL` | Easypanel VPS (`auto-financeiro`) |

## Relacionado

- [GUIA_INTEGRACAO_BANNERS_HTTP.md](./GUIA_INTEGRACAO_BANNERS_HTTP.md)
