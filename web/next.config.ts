import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  experimental: {
    // Dify Agent 응답(RAG + tool call + 로컬 LLM)은 10~60초 걸릴 수 있다. 기본 30초 → 3분
    proxyTimeout: 180_000,
  },
  // 브라우저(클라이언트 컴포넌트)의 /api/* 요청을 FastAPI backend로 전달
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
