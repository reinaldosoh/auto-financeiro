import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.4";

export const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-cron-key",
};

export function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

/**
 * Valida JWT do usuário. Retorna { userId, supabase } ou Response 401.
 * O cliente retornado usa o JWT do chamador (respeita RLS).
 */
export async function requireUser(req: Request): Promise<
  | {
    userId: string;
    email: string | null;
    supabase: ReturnType<typeof createClient>;
    supabaseAdmin: ReturnType<typeof createClient>;
  }
  | Response
> {
  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) {
    return jsonResponse({ error: "Unauthorized" }, 401);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

  const supabase = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
  });
  const supabaseAdmin = createClient(supabaseUrl, serviceKey);

  const token = authHeader.replace("Bearer ", "");
  const { data, error } = await supabase.auth.getClaims(token);
  if (error || !data?.claims?.sub) {
    return jsonResponse({ error: "Unauthorized" }, 401);
  }
  return {
    userId: data.claims.sub as string,
    email: (data.claims.email as string) ?? null,
    supabase,
    supabaseAdmin,
  };
}

/**
 * Verifica se o usuário pertence à mesma empresa da cidade,
 * ou é franqueado vinculado, ou é admin global.
 */
export async function ensureAcessoCidade(
  supabaseAdmin: ReturnType<typeof createClient>,
  userId: string,
  cidadeId: string,
): Promise<boolean> {
  const { data, error } = await supabaseAdmin.rpc("has_cidade_acesso", {
    _user_id: userId,
    _cidade_id: cidadeId,
  });
  if (error) {
    console.error("has_cidade_acesso erro:", error);
    return false;
  }
  return !!data;
}

/**
 * Verifica se o usuário pertence à mesma empresa do recurso (campanha, etc).
 */
export async function ensureMesmaEmpresa(
  supabaseAdmin: ReturnType<typeof createClient>,
  userId: string,
  empresaId: string,
): Promise<boolean> {
  const { data: isAdmin } = await supabaseAdmin.rpc("has_role", {
    _user_id: userId,
    _role: "admin",
  });
  if (isAdmin) return true;
  const { data: empresaUsuario } = await supabaseAdmin.rpc("empresa_do_usuario", {
    _user_id: userId,
  });
  return empresaUsuario === empresaId;
}

/** Valida header `x-cron-key` contra CRON_SECRET. */
export function requireCronSecret(req: Request): Response | null {
  const expected = Deno.env.get("CRON_SECRET");
  if (!expected) return jsonResponse({ error: "CRON_SECRET não configurado" }, 500);
  const got = req.headers.get("x-cron-key");
  if (got !== expected) return jsonResponse({ error: "Forbidden" }, 403);
  return null;
}
