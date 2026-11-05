import type { NextConfig } from "next";

const API = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Browser talks to /api/*; Next proxies to FastAPI so provider keys never reach the client.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};

export default nextConfig;
