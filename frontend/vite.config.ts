import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Vite config: path alias `@` -> src for clean imports, and a dev proxy
// so the frontend can call the FastAPI backend without CORS friction in
// local development. Production uses VITE_API_BASE_URL directly.
export default defineConfig(({ mode }) => {
  // Root .env is the platform's single configuration source for local Vite
  // development and Compose build arguments.
  const envDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const env = loadEnv(mode, envDir, "");
  const apiHost = env.OMNITRACK_LOCAL_API_HOST || "127.0.0.1";
  const apiPort = env.OMNITRACK_API_PORT || "8000";

  return {
    envDir,
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: Number(env.OMNITRACK_FRONTEND_DEV_PORT || "5173"),
      proxy: {
        "/api": {
          target: `http://${apiHost}:${apiPort}`,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ""),
        },
        "/ws": {
          target: `ws://${apiHost}:${apiPort}`,
          ws: true,
          rewrite: (p) => p.replace(/^\/ws/, ""),
        },
      },
    },
  };
});
