-- Migração para o Supabase do machine-finance-hub.
-- Cobrança de royalties via VPS: um único login (matriz) que troca de central por cidade.
-- Aplicar em supabase/migrations do finance-hub (ou no SQL Editor do projeto).

-- 1) Cada cidade passa a ser identificada pela bandeira da Machine (seletor de centrais).
ALTER TABLE public.cidades
  ADD COLUMN IF NOT EXISTS bandeira_id text;

CREATE UNIQUE INDEX IF NOT EXISTS cidades_empresa_bandeira_uidx
  ON public.cidades (empresa_id, bandeira_id)
  WHERE bandeira_id IS NOT NULL;

-- 2) E-mail/senha por cidade deixam de ser necessários (login é o da matriz, com 2FA do dono).
ALTER TABLE public.cidades ALTER COLUMN senha DROP NOT NULL;

-- 3) Evita buscar a mesma cobrança do mês duas vezes (a VPS consulta antes de inserir).
CREATE INDEX IF NOT EXISTS cobrancas_cidade_cenario_periodo_idx
  ON public.cobrancas (cidade_id, cenario, periodo_rotulo);

-- Preencher bandeira_id das cidades existentes (ids do seletor da matriz Ubiz Car Brasil).
-- Lista completa: GET /financeiro/centrais na VPS. Exemplo:
-- UPDATE public.cidades SET bandeira_id = '2796' WHERE nome ILIKE '%Alfenas%';
