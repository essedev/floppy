/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 'fake' for the in-memory API, 'http' for the control plane. */
  readonly VITE_API?: 'fake' | 'http'
  /** Base URL of the control plane API; defaults to /api/v1 on the same origin. */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
