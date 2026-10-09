// TEST-ONLY. A stand-in for the FastAPI backend behind the real BFF, used by
// e2e/refresh-reload.spec.ts (F13). Never imported by app code: the static
// guard lib/testOnlyImports.test.ts fails if anything outside e2e/ or the
// Playwright config does. Binds to 127.0.0.1 only.
//
// /auth/refresh follows the real rotation rules (backend/app/services/auth.py),
// simplified to one refresh family:
// - a live token is rotated the moment the request arrives (the server commits
//   before it answers), and the answer is sent after RESPONSE_DELAY_MS with the
//   new token in Set-Cookie;
// - a rotated token presented again within REUSE_GRACE_MS of its rotation, while
//   its replacement is live, gets 409 (a benign duplicate);
// - later, it is reuse: the family is revoked (401), as on the stand.
// REUSE_GRACE_MS is 2 s here (10 s in production) so the test runs in seconds.
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

/** Not used by any CI service (Postgres 5432, Redis 6379, the e2e web 3100). */
export const FAKE_BACKEND_PORT = 47613;
export const FAKE_BACKEND_URL = `http://127.0.0.1:${FAKE_BACKEND_PORT}`;

const RESPONSE_DELAY_MS = 1_500;
const REUSE_GRACE_MS = 2_000;

export const USER = {
  id: "u-fake",
  email: "storm@betpulse.dev",
  role: "user",
  totp_enabled: false,
  must_change_password: false,
  two_factor_required: false,
};

export interface FakeEvent {
  type: "rotated" | "conflict" | "reuse" | "revoked" | "unknown";
  presented: string | null;
  issued?: string;
  at: number;
}

interface TokenState {
  rotatedTo: string | null;
  rotatedAt: number | null;
}

export class FakeBackend {
  readonly events: FakeEvent[] = [];
  private server: Server | null = null;
  private tokens = new Map<string, TokenState>();
  private revoked = false;
  private counter = 1;
  private waiters: { count: number; resolve: () => void }[] = [];

  /** The family starts with one live token, T1. */
  readonly first = "T1";

  constructor() {
    this.tokens.set(this.first, { rotatedTo: null, rotatedAt: null });
  }

  /** The newest live token of the family, or null once it is revoked. */
  get liveToken(): string | null {
    if (this.revoked) return null;
    for (const [token, state] of this.tokens) if (state.rotatedTo === null) return token;
    return null;
  }

  /** Resolves once ``count`` rotations have been committed (requests received). */
  rotations(count: number): Promise<void> {
    if (this.events.filter((e) => e.type === "rotated").length >= count) return Promise.resolve();
    return new Promise((resolve) => this.waiters.push({ count, resolve }));
  }

  async start(): Promise<void> {
    this.server = createServer((req, res) => void this.handle(req, res));
    await new Promise<void>((resolve, reject) => {
      this.server!.once("error", (error: NodeJS.ErrnoException) => {
        reject(
          error.code === "EADDRINUSE"
            ? new Error(
                `The e2e fake backend cannot bind 127.0.0.1:${FAKE_BACKEND_PORT}: the port is taken. ` +
                  "Stop whatever listens there (or a previous test run) and try again.",
              )
            : error,
        );
      });
      this.server!.listen(FAKE_BACKEND_PORT, "127.0.0.1", () => resolve());
    });
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => (this.server ? this.server.close(() => resolve()) : resolve()));
    this.server = null;
  }

  private record(event: Omit<FakeEvent, "at">): void {
    this.events.push({ ...event, at: Date.now() });
    const rotated = this.events.filter((e) => e.type === "rotated").length;
    this.waiters = this.waiters.filter((w) => {
      if (rotated < w.count) return true;
      w.resolve();
      return false;
    });
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", FAKE_BACKEND_URL);
    if (req.method === "POST" && url.pathname === "/auth/refresh") return this.refresh(req, res);
    if (req.method === "GET" && url.pathname === "/matches") {
      return this.json(res, 200, { items: [], total: 0, limit: 30, offset: 0, matches_remaining: null });
    }
    if (req.method === "GET" && url.pathname === "/auth/me") return this.json(res, 200, USER);
    return this.json(res, 404, { detail: "Not Found" });
  }

  private async refresh(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const presented = /(?:^|;\s*)bp_refresh=([^;]+)/.exec(req.headers.cookie ?? "")?.[1] ?? null;
    const state = presented ? this.tokens.get(presented) : undefined;
    if (presented === null || state === undefined) {
      this.record({ type: "unknown", presented });
      return this.json(res, 401, { detail: "Invalid refresh token" });
    }
    if (this.revoked) {
      this.record({ type: "revoked", presented });
      return this.json(res, 401, { detail: "Invalid refresh token" });
    }
    if (state.rotatedTo !== null) {
      const replacementLive = this.tokens.get(state.rotatedTo)?.rotatedTo === null;
      if (replacementLive && Date.now() - (state.rotatedAt ?? 0) <= REUSE_GRACE_MS) {
        this.record({ type: "conflict", presented });
        return this.json(res, 409, { detail: "Refresh already in progress" });
      }
      this.revoked = true;
      this.record({ type: "reuse", presented });
      return this.json(res, 401, { detail: "Invalid refresh token" });
    }
    // Rotate now (the server commits before it answers), answer later.
    this.counter += 1;
    const issued = `T${this.counter}`;
    state.rotatedTo = issued;
    state.rotatedAt = Date.now();
    this.tokens.set(issued, { rotatedTo: null, rotatedAt: null });
    this.record({ type: "rotated", presented, issued });
    await new Promise((resolve) => setTimeout(resolve, RESPONSE_DELAY_MS));
    res.statusCode = 200;
    res.setHeader("content-type", "application/json");
    res.setHeader("set-cookie", [
      `bp_refresh=${issued}; Path=/auth/refresh; HttpOnly; SameSite=Strict`,
      "bp_csrf=c1; Path=/; SameSite=Strict",
    ]);
    res.end(JSON.stringify({ access_token: `access-${issued}`, token_type: "bearer", expires_in: 900, user: USER }));
  }

  private json(res: ServerResponse, status: number, body: unknown): void {
    res.statusCode = status;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(body));
  }
}
