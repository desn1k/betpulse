import createNextIntlPlugin from "next-intl/plugin";
import type { NextConfig } from "next";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

const securityHeaders = [
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emit a standalone server bundle for a small production Docker image.
  output: "standalone",
  // The build traces `typescript` only because this config file is TypeScript;
  // the standalone server inlines the compiled config and never loads it, so a
  // devDependency would otherwise ship in the image (guarded by
  // next.config.test.ts; the CI dependency gate audits production deps only).
  outputFileTracingExcludes: {
    "*": ["node_modules/typescript/**"],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
