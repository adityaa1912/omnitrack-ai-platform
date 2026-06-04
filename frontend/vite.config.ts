import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Vite config: path alias `@` -> src for clean imports, and a dev proxy
// so the frontend can call the FastAPI backend without CORS friction in
// local development. Production uses VITE_API_BASE_URL directly.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
        rewrite: (p) => p.replace(/^\/ws/, ""),
      },
    },
  },
});
