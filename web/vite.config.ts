import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, searchForWorkspaceRoot } from 'vite'

const src = fileURLToPath(new URL('./src', import.meta.url))
// The shared tokens and marks live at the repo root (DECISIONS #9): imported, never copied.
const design = fileURLToPath(new URL('../design', import.meta.url))

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
  },
  preview: { port: 4508, strictPort: true },
})
