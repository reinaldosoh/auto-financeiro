-- Histórico + deduplicação de alertas operacionais enviados ao n8n.
CREATE TABLE IF NOT EXISTS public.corridas_alertas_enviados (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cidade_id uuid NOT NULL REFERENCES public.cidades(id) ON DELETE CASCADE,
  empresa_id uuid REFERENCES public.empresas(id) ON DELETE SET NULL,
  os_id text NOT NULL,
  alerta_hash text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  enviado_em timestamptz NOT NULL DEFAULT now(),
  enviado_n8n_em timestamptz,
  UNIQUE (cidade_id, os_id, alerta_hash)
);

CREATE INDEX IF NOT EXISTS idx_corridas_alertas_enviados_empresa
  ON public.corridas_alertas_enviados (empresa_id, enviado_em DESC);

CREATE INDEX IF NOT EXISTS idx_corridas_alertas_enviados_cidade
  ON public.corridas_alertas_enviados (cidade_id, enviado_em DESC);

ALTER TABLE public.corridas_alertas_enviados ENABLE ROW LEVEL SECURITY;

-- Apenas service role / admin usa esta tabela (cron).
COMMENT ON TABLE public.corridas_alertas_enviados IS
  'Alertas operacionais do painel Machine já enviados ao n8n (dedup por cidade+OS+hash).';
