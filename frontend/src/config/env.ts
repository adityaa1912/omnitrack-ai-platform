/**
 * Centralized, typed access to environment configuration.
 *
 * All `import.meta.env` reads funnel through here so the rest of the app
 * never touches raw env vars directly. Defaults target the Vite dev proxy.
 */

function readEnv(key: string, fallback: string): string {
  const value = import.meta.env[key as keyof ImportMetaEnv] as string | undefined;
  return value && value.length > 0 ? value : fallback;
}

export const env = {
  apiBaseUrl: readEnv("VITE_API_BASE_URL", "/api"),
  wsBaseUrl: readEnv("VITE_WS_BASE_URL", "/ws"),
} as const;
