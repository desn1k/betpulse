import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emit a standalone server bundle for a small production Docker image.
  output: "standalone",
};

export default nextConfig;
