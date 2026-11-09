/**
 * Minimal JWT auth helpers for the frontend.
 *
 * REST calls carry `Authorization: Bearer <access_token>` when a token is
 * present; the legacy `X-API-Key` header is still sent alongside it so
 * API-key-only development keeps working unchanged. Tokens are persisted in
 * localStorage and cleared on logout.
 *
 * Endpoints: POST /auth/login (returns { access_token, ... }),
 * POST /auth/logout (revokes the bearer token's session).
 */

const TOKEN_KEY = "omnitrack_access_token";

export function getAccessToken(): string | null {
  if (typeof localStorage === "undefined") return null;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAccessToken(token: string | null): void {
  if (typeof localStorage === "undefined") return;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — best effort */
  }
}

export function isAuthenticated(): boolean {
  return !!getAccessToken();
}

function apiBase() {
  return import.meta.env.VITE_API_BASE_URL
    ? String(import.meta.env.VITE_API_BASE_URL).replace(/\/$/, "")
    : "/api";
}

export async function login(username: string, password: string) {
  const res = await fetch(`${apiBase()}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(body || `Login failed (${res.status})`);
  }
  const data = await res.json();
  if (!data?.access_token) throw new Error("Login response missing access_token");
  setAccessToken(data.access_token);
  return data;
}

export async function logout() {
  const token = getAccessToken();
  try {
    await fetch(`${apiBase()}/auth/logout`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  } finally {
    setAccessToken(null);
  }
}
