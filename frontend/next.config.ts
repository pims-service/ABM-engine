import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server output keeps the Docker image small.
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
