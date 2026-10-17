import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, searchForWorkspaceRoot } from 'vite'

const src = fileURLToPath(new URL('./src', import.meta.url))
// The shared tokens and marks live at the repo root (DECISIONS #9): imported, never copied.
const design = fileURLToPath(new URL('../design', import.meta.url))
// The control plane in development, for VITE_API=http: same origin through the proxy, so the
// session cookie works as in production. FLOPPY_API points it at another server.
const api = process.env.FLOPPY_API ?? 'http://127.0.0.1:4507'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': src, '@design': design },
  },
  server: {
    // Port from the project's Portsage range (4500-4509).
    port: 4508,
    strictPort: true,
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), design] },
    proxy: { '/api': { target: api, changeOrigin: false } },
  },
  preview: { port: 4508, strictPort: true },
})
