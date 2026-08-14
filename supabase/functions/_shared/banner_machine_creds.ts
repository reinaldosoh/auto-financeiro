export const DEFAULT_MACHINE_BASE = "https://reinaldo-automachine.sw5bxa.easypanel.host";

/** Base URL da VPS de automação: env AUTOMATION_URL com fallback explícito. */
export function resolverBaseUrlAutomacao(): string {
  const env = Deno.env.get("AUTOMATION_URL")?.trim().replace(/\/+$/, "");
  return env || DEFAULT_MACHINE_BASE;
}

export type CredenciaisMachine = {
  email: string;
  senha: string;
  chaveSecreta: string;
  baseUrl: string;
};

/** Credencial única do painel TaxiMachine, sempre a nível de EMPRESA. */
export function resolverCredenciaisMachine(empresa: Record<string, unknown> | null): CredenciaisMachine {
  return {
    email: String(empresa?.machine_painel_email ?? empresa?.machine_usuario ?? "").trim(),
    senha: String(empresa?.machine_painel_senha ?? empresa?.machine_senha ?? "").trim(),
    chaveSecreta: String(empresa?.machine_painel_totp ?? "").replace(/\s+/g, ""),
    baseUrl: resolverBaseUrlAutomacao(),
  };
}

export const MSG_CREDENCIAL_AUSENTE =
  "Configure e-mail e senha em Integrações > Credencial Machine e clique Validar acesso";
