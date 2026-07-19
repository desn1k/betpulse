/**
 * Build the strict, nonce-based CSP used for rendered Next.js responses.
 *
 * Development keeps the two allowances required by the Next.js toolchain.
 * Production does not permit inline or evaluated scripts.
 */
export function buildContentSecurityPolicy(nonce: string, isDevelopment: boolean): string {
  const directives = [
    "default-src 'self'",
    `script-src 'nonce-${nonce}' 'strict-dynamic' 'self'${isDevelopment ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' ${isDevelopment ? "'unsafe-inline'" : `'nonce-${nonce}'`}`,
    "img-src 'self' blob: data:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "frame-src 'none'",
    ...(isDevelopment ? [] : ["upgrade-insecure-requests"]),
  ];

  return directives.map((directive) => `${directive};`).join(" ");
}

export function createCspNonce(): string {
  return Buffer.from(crypto.randomUUID()).toString("base64");
}

export function withCspRequestHeaders(
  source: Headers,
  nonce: string,
  contentSecurityPolicy: string,
): Headers {
  const headers = new Headers(source);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", contentSecurityPolicy);
  return headers;
}
