/*
  Picks the API implementation once, at startup. VITE_API=fake uses the in-memory stand-in
  (fake/), VITE_API=http the real control plane; without it, `pnpm dev` runs the fake and a
  build talks HTTP, so a production bundle never ships the fake unless asked to.
*/

import type { Api } from './client'
import { httpApi } from './http'

/** Which implementation runs. */
export const apiMode: 'fake' | 'http' =
  (import.meta.env.VITE_API ?? (import.meta.env.DEV ? 'fake' : 'http')) === 'fake' ? 'fake' : 'http'

// Dynamic import: the fake stays out of an HTTP build's bundle.
const fake = apiMode === 'fake' ? await import('./fake') : null

/** The API the features call. */
export const api: Api = fake?.fakeApi ?? httpApi

/** Controls of the fake API (reset, network failure, last email), null when talking HTTP. */
export const fakeControl = fake?.fakeControl ?? null
export type { FakeMail } from './fake'

export { ApiError, isApiError, type Api } from './client'
export type * from './types'
