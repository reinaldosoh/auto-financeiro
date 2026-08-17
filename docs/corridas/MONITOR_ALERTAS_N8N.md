# Monitor de alertas operacionais → n8n

Varre **todas as cidades ativas** a cada **15 minutos**, detecta corridas com alerta no painel Machine e envia ao n8n.

## Fluxo

```
pg_cron (15 min)
  → Edge Function monitor-corridas-alertas (x-cron-key)
  → login VPS por empresa
  → POST /dashboard-v2/monitor-alertas (todas cidades da empresa)
  → dedup em corridas_alertas_enviados
  → POST n8n (só alertas novos)
```

## Payload enviado ao n8n

```json
{
  "cidade_id": "uuid",
  "cidade_nome": "Mariana - MG",
  "empresa_id": "uuid",
  "empresa_nome": "Ubiz Car",
  "bandeira": "Ubiz Car - Mariana",
  "alerta": "Motorista iniciou corrida distante do local de embarque",
  "alertas": ["Motorista iniciou corrida distante do local de embarque"],
  "status_corrida": "Em andamento",
  "numero_os": "749744134",
  "link_rastreio": "https://cloud.machine.global/solicitacao/acompanhar/...",
  "passageiro": { "nome": "...", "telefone": "..." },
  "motorista": { "nome": "...", "telefone": "..." },
  "detectado_em": "2026-08-17T13:45:00.000Z"
}
```

`link_rastreio` vem `null` quando status contém **cancelad** ou **finaliz**.

## Deploy

### 1. Migration (Supabase SQL Editor)

Executar `supabase/migrations/20260817104500_corridas_alertas_enviados.sql`.

### 2. Edge Function

```bash
supabase functions deploy monitor-corridas-alertas --no-verify-jwt
```

Secrets:

| Secret | Valor |
|--------|--------|
| `CRON_SECRET` | chave longa aleatória |
| `ALERTAS_N8N_WEBHOOK_URL` | URL do webhook n8n (opcional; default já configurado) |
| `AUTOMATION_URL` | `https://reinaldo-automachine.sw5bxa.easypanel.host` |

### 3. VPS

Redeploy `reinaldo-automachine` com endpoint `POST /dashboard-v2/monitor-alertas`.

### 4. Cron 15 min (Supabase → Database → Extensions → pg_cron)

Com **pg_net** habilitado:

```sql
SELECT cron.schedule(
  'monitor-corridas-alertas-15m',
  '*/15 * * * *',
  $$
  SELECT net.http_post(
    url := 'https://SEU_PROJECT.supabase.co/functions/v1/monitor-corridas-alertas',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'x-cron-key', 'SEU_CRON_SECRET'
    ),
    body := '{}'::jsonb
  );
  $$
);
```

Teste manual:

```bash
curl -X POST "https://SEU_PROJECT.supabase.co/functions/v1/monitor-corridas-alertas" \
  -H "Content-Type: application/json" \
  -H "x-cron-key: SEU_CRON_SECRET" \
  -d '{}'
```

## Deduplicação

Mesmo alerta (mesma OS + mesmo texto) **não reenvia**. Se surgir **novo** texto de intercorrência na mesma OS, gera hash diferente e envia de novo.

## Filtro temporal

Janela padrão: **4 horas** (`HORAS_FILTRO` na edge function).
