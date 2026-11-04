import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  // pin the workspace root: a stray lockfile higher up (e.g. in the home folder) would be picked
  outputFileTracingRoot: path.dirname(fileURLToPath(import.meta.url)),
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    // Our own PDFs (template previews, generated documents) are shown in an <iframe> on our own
    // pages: same-origin framing only, everything else stays DENY.
    const sameOriginFrame = [
      { key: "X-Frame-Options", value: "SAMEORIGIN" },
      { key: "Content-Security-Policy", value: "frame-ancestors 'self'" },
    ];
    return [
      { source: "/:path*", headers: securityHeaders },
      { source: "/api/v1/templates/:id/preview.pdf", headers: sameOriginFrame },
      { source: "/api/v1/documents/:id/pdf", headers: sameOriginFrame },
    ];
  },
};

export default withNextIntl(nextConfig);
