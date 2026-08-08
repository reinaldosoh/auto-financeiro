# Documentação — Banners TaxiMachine via API HTTP

Guias para times que operam o painel **cloud.taximachine.com.br** e querem automatizar banners **sem depender de Selenium** no dia a dia.

| Documento | Conteúdo |
|-----------|----------|
| **[GUIA_INTEGRACAO_BANNERS_HTTP.md](./GUIA_INTEGRACAO_BANNERS_HTTP.md)** | Guia principal: deploy do servidor, endpoints internos da Machine, 2FA, implementação por tipo de banner |
| **[REFERENCIA_CAMPOS_PAINEL.md](./REFERENCIA_CAMPOS_PAINEL.md)** | Tabela de IDs/campos DOM e parâmetros de upload por recurso |
| **[LOVABLE_CREDENCIAIS_MULTI_EMPRESA.md](./LOVABLE_CREDENCIAIS_MULTI_EMPRESA.md)** | Credencial Machine **única por empresa** (menu separado), 2FA automático |

## Referência de implementação

Repositório de exemplo (Radar Mobility / auto-financeiro):

- [github.com/reinaldosoh/auto-financeiro](https://github.com/reinaldosoh/auto-financeiro)
- Login HTTP já implementado: `machine_notificacao_http.py` → `login_painel()`
- Banners ainda via Selenium: `auto_2fa.py` (referência de campos e fluxo do painel)

## Resumo em uma frase

**Guarde o segredo TOTP uma vez → faça login HTTP com `pyotp` → use cookie `PHPSESSID` para chamar os endpoints internos de `/bandeira/*` e gravar anúncios/campanhas.**
