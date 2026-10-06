# Hardening da API Machine — rollout coordenado

Correções locais: autenticação X-API-Key em todas as rotas antes do parse do corpo; docs desligadas; /codigo e /chaves desligados por padrão; TOTP criptografado com Fernet, gravação atômica e lock; remoção dos logs de segredo/código; downloads HTTPS públicos com DNS/IP fixado, validação de certificado, redirects revalidados e limite de 5 MiB; base64 5 MiB e corpo HTTP 8 MiB.

## Variáveis obrigatórias

- `MACHINE_API_KEY`: segredo aleatório de pelo menos 32 caracteres, compartilhado somente entre esta API e os consumidores backend. Não vai no Vite, frontend, URL, Git ou chat.
- `TOTP_ENCRYPTION_KEY`: chave Fernet (32 bytes codificados em base64 URL-safe). Alternativa: `TOTP_ENCRYPTION_KEY_FILE` apontando ao arquivo montado pelo cofre. Configure só uma dessas fontes.
- `TOTP_STORE_PATH`: caminho do store em diretório persistente, incluindo arquivo .lock e temporários necessários para rename atômico. Monte o diretório, não um arquivo isolado.
- `AUTOMATION_URL` nos consumidores: origem HTTPS confiável desta API. `automationFetch` não envia a chave a outra origem nem segue redirect. HTTP somente para localhost explicitamente configurado.
- `MACHINE_ENABLE_TOTP_ADMIN`: manter ausente/0. O valor 1 reabre /codigo e /chaves somente com API key; nenhum consumidor identificado depende deles.

## Ordem sem quebrar integração

1. Guarde as duas novas chaves em cofre/secret manager. Não regenerar a chave Fernet a cada restart: isso torna o store ilegível.
2. Configure `MACHINE_API_KEY` nos consumidores backend Supabase e no serviço da VPS. Publique primeiro os consumidores preparados com X-API-Key: a versão antiga da API aceita e ignora esse header, mantendo compatibilidade durante a transição.
3. Pause somente a API Machine durante a migração do store. Com `TOTP_ENCRYPTION_KEY` ou `_FILE` já injetada no ambiente, rode `python totp_store.py migrate /CAMINHO/PERSISTENTE/chaves_totp.json`. A migração lê o legado somente nesta ação explícita, verifica roundtrip antes de gravar e substitui por ciphertext com chmod 0600. Não mantém cópia plaintext. Faça backup protegido em cofre antes da janela; não fazer cp do plaintext para arquivo público/log/Git.
4. Publique a API nova com requirements atualizados e o diretório persistente correto. Reinicie só após confirmar que a mesma chave Fernet está montada e que o store criptografado foi preservado. Nenhuma função lê legado plaintext automaticamente.
5. Revalide HTTP: sem chave/errada ->401, chave correta /health ->200, /codigo e /chaves ->404, /docs e /openapi.json ->404. Healthcheck de proxy precisa enviar X-API-Key; não criar exceção pública para rotas operacionais.
6. Faça smoke de consumidor com conta sintética e sem envio/apagamento de campanha. Verifique login e um endpoint de leitura. Não disparar jobs globais.

## Status de ativação (2026-10-06)

| Passo | Escopo | Situação |
|-------|--------|----------|
| 1–2 | Cofre + `MACHINE_API_KEY` / `AUTOMATION_URL` no Supabase (`lxapogvzccnusjmbthnj`) | Feito — chaves no cofre local (personal store), **não** no Git/chat |
| 2 | Consumidores backend com `automationFetch` (10 edges) | Publicados — ver `notification_ubiz` `b710490` |
| 2 | `MACHINE_API_KEY` no Easypanel / VPS | **Feito** (2026-10-06) — env salvo + redeploy; bate com Supabase |
| 3–4 | Pause API, backup, `totp_store.py migrate`, redeploy `auto-financeiro` `ab3e7af` | **Feito** (2026-10-06) — legado VPS/backups Easypanel **não encontrado**; store **reconstruído** (3 contas) a partir de `empresas.machine_painel_totp` no Supabase, backup no cofre local, volume `/data/totp/chaves_totp.json` **Fernet + mode 600**, `entries=3` |
| 5 | Revalidação HTTP na URL publicada (401/404/200) | **Feito** |
| 6 | Smoke leitura sintética | **Feito** (2026-10-06) — `www.radarmobility.app`, **VAMBORA**: Integrações → validar OK; `/dashboard` com indicadores D-1 e tempo real (leitura), sem notificações/campanhas/jobs |

Chave Fernet para migração TOTP: gerada uma vez no cofre local; **não regenerar** no redeploy. Apagar secret de teste `TEST_FORMAT` no painel Supabase se ainda existir.

## Testes executados

Instale o ambiente de teste com `python -m pip install -r requirements-dev.txt`.

`python -m unittest tests_security -v`: 18 testes, incluindo cobertura de todas as 50 rotas com chave ausente/errada, criptografia/tampering/migração, concorrência do store, SSRF com DNS misto, IP fixado, redirect, TLS e tamanhos.

Servidor temporário loopback com segredos/arquivo sintéticos: 7 checks HTTP reais. Processo encerrado e store removido. Nenhuma conta Machine foi usada.

Cliente Deno automationFetch: teste com fetch mockado comprova header, destino confiável, ausência de chave e redirect=error.

## Limites e itens fora desta mudança

**Proteção X-API-Key na VPS está ativa** (passo 5 OK). Store TOTP no volume **migrado** (Fernet, `chmod 600`, 3 e-mails). Healthcheck do proxy Easypanel sem header customizado na tela Advanced — monitorar externamente se necessário.

A API key autentica consumidores backend de confiança, não usuários/tenants. As edges precisam continuar validando JWT/empresa/cidade; não oferecer esta chave ao navegador. Respostas de login preservam o contrato backend TOTP existente: não encaminhar o segredo ao frontend.

Rate limit/lockout por conta, concorrência de jobs, session_token em query, revisão de todos os erros internos, container não-root e bind/proxy do ambiente permanecem fora das correções 1–3. O corpo de erro 500 dos handlers de anúncio foi reduzido; isso não significa que todos os str(e) do projeto foram eliminados. Logs antigos podem conter segredos: tratar sua exposição e rotacionar TOTP no painel/cofre com os titulares, sem simular que apagá-los remove cópias históricas.

O Python local usa LibreSSL e urllib3 emite aviso de runtime não suportado; testes de TLS são unitários/mocados. Validar o handshake real no runtime OpenSSL da imagem antes de afirmar integração externa aprovada.

Typecheck ampliado dos consumidores: 26 erros existentes (mesmos códigos/mensagens antes e depois da troca de header); não atribuir PASS global às edges. Helper novo testado isoladamente. Logs edge-check.log e edge-check-baseline.log. Nenhum erro adicional introduzido na comparação.
