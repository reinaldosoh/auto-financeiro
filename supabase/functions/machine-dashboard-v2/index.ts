// Proxy REST autenticado (JWT Radar) → VPS /dashboard-v2/* (Consultar corridas Machine).
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.4";
import { corsHeaders, jsonResponse, requireUser, ensureAcessoCidade } from "../_shared/auth.ts";
import {
  resolverCredenciaisMachine,
  MSG_CREDENCIAL_AUSENTE,
  type CredenciaisMachine,
} from "../_shared/banner_machine_creds.ts";

const TIMEOUT_MS = 5 * 60 * 1000;
const SESSAO_CACHE_MS = 25 * 60 * 1000;

type Acao =
  | "inicializar"
  | "bandeiras"
  | "filtro"
  | "corridas"
  | "detalhe"
  | "posicao";

type SupabaseAdmin = ReturnType<typeof createClient>;

type Ctx = {
  cidade: { id: string; nome: string; empresa_id: string | null; bandeira_machine_id?: number | string | null };
  creds: CredenciaisMachine;
  empresaId: string;
};

type SessaoCache = { token: string; exp: number };
const sessoesPorEmpresa = new Map<string, SessaoCache>();
const loginEmAndamento = new Map<string, Promise<SessaoCache>>();

async function chamar(
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
  } catch (err) {
    return { status: 0, body: { erro: (err as Error).message } };
  } finally {
    clearTimeout(timer);
  }
}

function extrairPayloadVps(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object") return {};
  const raw = body as Record<string, unknown>;
  const detail = raw.detail;
  if (detail && typeof detail === "object") {
    return Array.isArray(detail) ? (detail[0] as Record<string, unknown>) ?? {} : detail as Record<string, unknown>;
  }
  return raw;
}

function mensagemErro(body: unknown): string {
  const p = extrairPayloadVps(body);
  return String(p.mensagem ?? p.erro ?? p.detail ?? "").trim();
}

function sessaoExpirada(r: { status: number; body: unknown }): boolean {
  if (r.status === 401) return true;
  const msg = mensagemErro(r.body).toLowerCase();
  return msg.includes("sessão expirada") || msg.includes("sessao expirada") || msg.includes("faça login") || msg.includes("faca login");
}

async function resolverContexto(supabaseAdmin: SupabaseAdmin, cidadeId: string) {
  const { data: cidade, error: errCidade } = await supabaseAdmin
    .from("cidades")
    .select("id, nome, empresa_id, bandeira_machine_id")
    .eq("id", cidadeId)
    .maybeSingle();
  if (errCidade || !cidade) {
    return { error: jsonResponse({ error: "Cidade não encontrada" }, 404) as Response };
  }
  if (!cidade.empresa_id) {
    return { error: jsonResponse({ error: "Cidade sem empresa vinculada" }, 400) as Response };
  }

  const { data: empresa } = await supabaseAdmin
    .from("empresas")
    .select("id, nome, machine_painel_email, machine_painel_senha, machine_painel_totp, machine_usuario, machine_senha")
    .eq("id", cidade.empresa_id)
    .maybeSingle();

  const creds = resolverCredenciaisMachine(empresa ?? null);
  if (!creds.email || !creds.senha) {
    return { error: jsonResponse({ error: MSG_CREDENCIAL_AUSENTE }, 400) as Response };
  }

  return { cidade, creds, empresaId: cidade.empresa_id } satisfies Ctx;
}

function bandeiraPadrao(cidade: { bandeira_machine_id?: number | string | null }, bandeiraId?: unknown) {
  if (bandeiraId !== undefined && bandeiraId !== null && bandeiraId !== "") return String(bandeiraId);
  if (cidade.bandeira_machine_id != null) return String(cidade.bandeira_machine_id);
  return undefined;
}

async function loginVps(baseUrl: string, creds: CredenciaisMachine): Promise<SessaoCache> {
  const r = await chamar(baseUrl, "/notificacao/login", {
    method: "POST",
    body: {
      email: creds.email,
      senha: creds.senha,
      chave_secreta: creds.chaveSecreta || undefined,
    },
  });
  const payload = extrairPayloadVps(r.body);
  const token = payload.session_token ?? payload.token;
  if (!token) throw new Error(mensagemErro(r.body) || "Falha no login Machine");
  return { token: String(token), exp: Date.now() + SESSAO_CACHE_MS };
}

async function obterSessao(ctx: Ctx, forcar = false): Promise<SessaoCache> {
  if (!forcar) {
    const cached = sessoesPorEmpresa.get(ctx.empresaId);
    if (cached && cached.exp > Date.now()) return cached;
  }
  const emCurso = loginEmAndamento.get(ctx.empresaId);
  if (emCurso) return emCurso;

  if (forcar) sessoesPorEmpresa.delete(ctx.empresaId);

  const promessa = (async () => {
    const sessao = await loginVps(ctx.creds.baseUrl, ctx.creds);
    sessoesPorEmpresa.set(ctx.empresaId, sessao);
    return sessao;
  })();

  loginEmAndamento.set(ctx.empresaId, promessa);
  try {
    return await promessa;
  } finally {
    if (loginEmAndamento.get(ctx.empresaId) === promessa) loginEmAndamento.delete(ctx.empresaId);
  }
}

/** Aplica filtro e pagina TODAS as páginas na mesma sessão VPS (1 invoke edge). */
async function filtrarPaginadoCompleto(
  ctx: Ctx,
  sessionToken: string,
  rest: Record<string, unknown>,
  bandeira_id?: string,
): Promise<{ status: number; body: unknown }> {
  const { baseUrl } = ctx.creds;
  const bodyFiltro = {
    session_token: sessionToken,
    bandeira_id: bandeira_id ?? rest.bandeira_id,
    horas: rest.horas ?? 0.25,
    filtro_matriz: rest.filtro_matriz,
    incluir_coordenadas: rest.incluir_coordenadas ?? true,
    enriquecer_alertas: rest.enriquecer_alertas ?? true,
  };

  const resultado = await chamar(baseUrl, "/dashboard-v2/filtro", { method: "POST", body: bodyFiltro });
  if (resultado.status >= 400) return resultado;

  const payload = extrairPayloadVps(resultado.body);
  const porId = new Map<string, Record<string, unknown>>();
  for (const c of (payload.corridas as unknown[]) ?? []) {
    if (c && typeof c === "object") {
      const id = String((c as Record<string, unknown>).id ?? "");
      if (id) porId.set(id, c as Record<string, unknown>);
    }
  }

  const meta = (payload.meta as Record<string, unknown>) ?? {};
  const total = Number(meta.total) || undefined;
  const paginasMeta = Number(meta.paginas) || undefined;
  const limite = paginasMeta ?? (total && porId.size ? Math.ceil(total / porId.size) : 1);

  for (let pagina = 2; pagina <= Math.min(50, limite); pagina += 1) {
    if (total && porId.size >= total) break;
    const pag = await chamar(baseUrl, "/dashboard-v2/corridas", {
      method: "POST",
      body: {
        session_token: sessionToken,
        page: pagina,
        incluir_coordenadas: bodyFiltro.incluir_coordenadas,
        apenas_ativos_mapa: false,
      },
    });
    if (pag.status >= 400) break;
    const pp = extrairPayloadVps(pag.body);
    const lote = (pp.corridas as unknown[]) ?? [];
    if (!lote.length) break;
    const antes = porId.size;
    for (const c of lote) {
      if (c && typeof c === "object") {
        const id = String((c as Record<string, unknown>).id ?? "");
        if (id) porId.set(id, c as Record<string, unknown>);
      }
    }
    if (porId.size === antes) break;
    if (total && porId.size >= total) break;
  }

  return {
    status: 200,
    body: {
      sucesso: true,
      corridas: Array.from(porId.values()),
      meta: { ...meta, total_listado: porId.size },
      filtro: payload.filtro,
    },
  };
}

async function executarComSessao(
  ctx: Ctx,
  sessionToken: string,
  acao: Acao,
  rest: Record<string, unknown>,
  bandeira_id?: string,
) {
  const { baseUrl } = ctx.creds;

  switch (acao) {
    case "bandeiras":
      return chamar(baseUrl, "/dashboard-v2/bandeiras", { query: { session_token: sessionToken } });
    case "filtro":
      return filtrarPaginadoCompleto(ctx, sessionToken, rest, bandeira_id);
    case "corridas": {
      const precisaFiltro =
        bandeira_id ||
        rest.horas != null ||
        rest.filtro_matriz != null;
      if (precisaFiltro) {
        return filtrarPaginadoCompleto(ctx, sessionToken, rest, bandeira_id);
      }
      return chamar(baseUrl, "/dashboard-v2/corridas", {
        method: "POST",
        body: {
          session_token: sessionToken,
          page: rest.page ?? 1,
          incluir_coordenadas: rest.incluir_coordenadas ?? true,
          apenas_ativos_mapa: rest.apenas_ativos_mapa ?? false,
        },
      });
    }
    case "detalhe":
      return chamar(baseUrl, `/dashboard-v2/corridas/${encodeURIComponent(String(rest.os_id))}`, {
        query: { session_token: sessionToken },
      });
    case "posicao":
      return chamar(baseUrl, `/dashboard-v2/corridas/${encodeURIComponent(String(rest.os_id))}/posicao`, {
        query: { session_token: sessionToken },
      });
    default:
      throw new Error(`Ação não suportada: ${acao}`);
  }
}

async function rodarAcao(ctx: Ctx, acao: Acao, rest: Record<string, unknown>) {
  const bandeira_id = bandeiraPadrao(ctx.cidade, rest.bandeira_id);

  if (acao === "inicializar") {
    const sessao = await obterSessao(ctx);
    const bandResp = await chamar(ctx.creds.baseUrl, "/dashboard-v2/bandeiras", {
      query: { session_token: sessao.token },
    });
    const bandPayload = extrairPayloadVps(bandResp.body);
    const horas = rest.horas ?? 0.25;
    let corridas: unknown = [];
    let meta: unknown = undefined;
    if (bandeira_id) {
      const filtro = await filtrarPaginadoCompleto(ctx, sessao.token, { horas }, bandeira_id);
      const filtroPayload = extrairPayloadVps(filtro.body);
      corridas = filtroPayload.corridas ?? [];
      meta = (filtroPayload.meta as Record<string, unknown> | undefined) ?? undefined;
    }
    return jsonResponse({
      status: 200,
      body: {
        sucesso: true,
        session_token: sessao.token,
        bandeira_id,
        bandeiras: bandPayload.bandeiras ?? [],
        corridas,
        meta,
        horas,
      },
    });
  }

  let sessao = await obterSessao(ctx);
  let resultado = await executarComSessao(ctx, sessao.token, acao, rest, bandeira_id);
  if (sessaoExpirada(resultado)) {
    sessoesPorEmpresa.delete(ctx.empresaId);
    sessao = await obterSessao(ctx, true);
    resultado = await executarComSessao(ctx, sessao.token, acao, rest, bandeira_id);
  }

  const payload = extrairPayloadVps(resultado.body);
  const httpStatus = resultado.status >= 400 ? resultado.status : 200;
  return jsonResponse({ status: httpStatus, body: payload }, httpStatus);
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const auth = await requireUser(req);
  if (auth instanceof Response) return auth;

  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return jsonResponse({ error: "Body inválido" }, 400);
  }

  const { acao, cidade_id, ...rest } = payload as { acao: Acao; cidade_id?: string; [k: string]: unknown };
  if (!acao) return jsonResponse({ error: "acao obrigatória" }, 400);
  if (!cidade_id) return jsonResponse({ error: "cidade_id obrigatória" }, 400);

  const podeAcessar = await ensureAcessoCidade(auth.supabaseAdmin, auth.userId, cidade_id);
  if (!podeAcessar) return jsonResponse({ error: "Sem acesso a esta cidade" }, 403);

  const ctx = await resolverContexto(auth.supabaseAdmin, cidade_id);
  if ("error" in ctx && ctx.error) return ctx.error;

  try {
    return await rodarAcao(ctx as Ctx, acao, rest);
  } catch (err) {
    return jsonResponse({
      status: 500,
      body: { erro: err instanceof Error ? err.message : String(err) },
    });
  }
});
