// Server-only helpers for reaching the FastAPI backend. The base URL is read
// from the environment (12-factor); it is never exposed to the browser — the
// browser talks to our own /api/* route handlers, which proxy to the backend.

export function backendBaseUrl(): string {
  return process.env.API_BASE_URL ?? "http://localhost:8000";
}

export async function backendGet(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${backendBaseUrl()}${path}`, {
    // Public read endpoints: always fetch fresh (live scores, in-play probs).
    cache: "no-store",
    headers: { accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
}
