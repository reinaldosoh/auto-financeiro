// Cron (15 min): monitora alertas operacionais de todas as cidades ativas e envia ao n8n.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.4";
import { corsHeaders, jsonResponse, requireCronSecret } from "../_shared/auth.ts";
import {
  resolverCredenciaisMachine,
  type CredenciaisMachine,
} from "../_shared/banner_machine_creds.ts";

const TIMEOUT_MS = 5 * 60 * 1000;
const HORAS_FILTRO = 4;
const N8N_DEFAULT =
  "https://n8n-webhook.api.soureino.com/webhook/e7767920-77c9-4ee0-a7a9-f297f2bcd86f";

type CidadeRow = {
  id: string;
  nome: string;
  estado: string | null;
  empresa_id: string;
  bandeira_machine_id: number | string | null;
  empresas: {
    id: string;
    nome: string;
    machine_painel_email: string | null;
    machine_painel_senha: string | null;
    machine_painel_totp: string | null;
    machine_usuario: string | null;
    machine_senha: string | null;
  };
};

type AlertaPayload = {
  cidade_id: string;
  cidade_nome: string;
  empresa_id: string;
  empresa_nome: string;
  bandeira: string;
  alerta: string;
  alertas: string[];
  status_corrida: string;
  numero_os: string;
  link_rastreio: string | null;
  passageiro: { nome: string; telefone: string };
  motorista: { nome: string; telefone: string };
};

async function chamarVps(
  baseUrl: string,
  path: string,
  init: { method?: string; body?: unknown; query?: Record<string, unknown> } = {},
) {
  const url = new URL(`${baseUrl}${path}`);
  if (init.query) {
    for (const [k, v] of Object.entries(init.query)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(url.toString(), {
      method: init.method ?? "GET",
      headers: init.body ? { "Content-Type": "application/json" } : undefined,
      body: init.body ? JSON.stringify(init.body) : undefined,
      signal: controller.signal,
    });
    const raw = await resp.json().catch(async () => await resp.text().catch(() => null));
    return { status: resp.status, body: raw };
  } finally {
    clearTimeout(timer);
  }
}

function extrairPayload(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object") return {};
  const raw = body as Record<string, unknown>;
  const detail = raw.detail;
  if (detail && typeof detail === "object") {
    return Array.isArray(detail) ? (detail[0] as Record<string, unknown>) ?? {} : (detail as Record<string, unknown>);
  }
  return raw;
}

function mensagemErro(body: unknown): string {
  const p = extrairPayload(body);
  return String(p.mensagem ?? p.erro ?? p.detail ?? "").trim();
}

async function loginVps(baseUrl: string, creds: CredenciaisMachine): Promise<string> {
  const r = await chamarVps(baseUrl, "/notificacao/login", {
    method: "POST",
    body: {
      email: creds.email,
      senha: creds.senha,
      chave_secreta: creds.chaveSecreta || undefined,
    },
  });
  const payload = extrairPayload(r.body);
  const token = payload.session_token ?? payload.token;
  if (!token) throw new Error(mensagemErro(r.body) || "Falha no login Machine");
  return String(token);
}

async function hashAlertas(alertas: string[]): Promise<string> {
  const joined = [...new Set(alertas.filter(Boolean))].sort().join("|");
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(joined || "sem-alerta"));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);
}

async function enviarN8n(url: string, payload: Record<string, unknown>) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30_000);
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const txt = await resp.text().catch(() => "");
      throw new Error(`n8n HTTP ${resp.status}: ${txt.slice(0, 200)}`);
    }
  } finally {
    clearTimeout(timer);
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });

  const cronBlock = requireCronSecret(req);
  if (cronBlock) return cronBlock;

  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const n8nUrl = Deno.env.get("ALERTAS_N8N_WEBHOOK_URL")?.trim() || N8N_DEFAULT;
  const supabase = createClient(supabaseUrl, serviceKey);

  const inicio = Date.now();
  const resumo = {
    empresas_processadas: 0,
    cidades_consultadas: 0,
    alertas_encontrados: 0,
    alertas_enviados: 0,
    alertas_duplicados: 0,
    erros: [] as string[],
  };

  try {
    const { data: cidadesRaw, error: errCidades } = await supabase
      .from("cidades")
      .select(
        "id, nome, estado, ativo, empresa_id, bandeira_machine_id, empresas!inner(id, nome, machine_painel_email, machine_painel_senha, machine_painel_totp, machine_usuario, machine_senha)",
      )
      .eq("ativo", true)
      .not("empresa_id", "is", null)
      .not("bandeira_machine_id", "is", null);

    if (errCidades) throw new Error(errCidades.message);

    const cidades = ((cidadesRaw ?? []) as CidadeRow[]).filter((c) => {
      const creds = resolverCredenciaisMachine(c.empresas);
      return creds.email && creds.senha;
    });

    const porEmpresa = new Map<string, CidadeRow[]>();
    for (const c of cidades) {
      const lista = porEmpresa.get(c.empresa_id) ?? [];
      lista.push(c);
      porEmpresa.set(c.empresa_id, lista);
    }

    for (const [empresaId, listaCidades] of porEmpresa) {
      const empresa = listaCidades[0].empresas;
      const creds = resolverCredenciaisMachine(empresa);
      resumo.empresas_processadas += 1;

      let sessionToken: string;
      try {
        sessionToken = await loginVps(creds.baseUrl, creds);
      } catch (e) {
        resumo.erros.push(`login ${empresa.nome}: ${(e as Error).message}`);
        continue;
      }

      const payloadCidades = listaCidades.map((c) => ({
        cidade_id: c.id,
        cidade_nome: c.estado ? `${c.nome} - ${c.estado}` : c.nome,
        bandeira_id: String(c.bandeira_machine_id),
        empresa_id: empresaId,
        empresa_nome: empresa.nome,
      }));

      resumo.cidades_consultadas += payloadCidades.length;

      const monitorResp = await chamarVps(creds.baseUrl, "/dashboard-v2/monitor-alertas", {
        method: "POST",
        body: {
          session_token: sessionToken,
          cidades: payloadCidades,
          horas: HORAS_FILTRO,
        },
      });

      const monitorBody = extrairPayload(monitorResp.body);
      if (monitorResp.status >= 400) {
        resumo.erros.push(`monitor ${empresa.nome}: ${mensagemErro(monitorResp.body) || monitorResp.status}`);
        continue;
      }

      const alertas = (monitorBody.alertas ?? []) as AlertaPayload[];
      resumo.alertas_encontrados += alertas.length;

      for (const alerta of alertas) {
        const alertaHash = await hashAlertas(alerta.alertas?.length ? alerta.alertas : [alerta.alerta]);
        const { error: insErr } = await supabase.from("corridas_alertas_enviados").insert({
          cidade_id: alerta.cidade_id,
          empresa_id: alerta.empresa_id,
          os_id: alerta.numero_os,
          alerta_hash: alertaHash,
          payload: alerta,
        });

        if (insErr) {
          if (insErr.code === "23505") {
            resumo.alertas_duplicados += 1;
            continue;
          }
          resumo.erros.push(`db ${alerta.numero_os}: ${insErr.message}`);
          continue;
        }

        try {
          await enviarN8n(n8nUrl, {
            ...alerta,
            detectado_em: new Date().toISOString(),
          });
          await supabase
            .from("corridas_alertas_enviados")
            .update({ enviado_n8n_em: new Date().toISOString() })
            .eq("cidade_id", alerta.cidade_id)
            .eq("os_id", alerta.numero_os)
            .eq("alerta_hash", alertaHash);
          resumo.alertas_enviados += 1;
        } catch (e) {
          resumo.erros.push(`n8n OS ${alerta.numero_os}: ${(e as Error).message}`);
        }
      }
    }

    return jsonResponse({
      ok: true,
      duracao_ms: Date.now() - inicio,
      n8n_url: n8nUrl.replace(/\/webhook\/[^/]+$/, "/webhook/***"),
      ...resumo,
    });
  } catch (err) {
    return jsonResponse(
      {
        ok: false,
        erro: err instanceof Error ? err.message : String(err),
        duracao_ms: Date.now() - inicio,
        ...resumo,
      },
      500,
    );
  }
});
