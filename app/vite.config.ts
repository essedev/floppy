import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import process from "node:process";

const host = process.env.TAURI_DEV_HOST;

// Tauri expects a fixed port and keeps its own error output on screen; the
// Rust side is not watched. design/ (shared tokens and marks) is outside app/.
export default defineConfig(() => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: { ignored: ["**/src-tauri/**"] },
    fs: { allow: [path.resolve(import.meta.dirname, "..")] },
  },
}));
