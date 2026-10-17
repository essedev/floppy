/* The storage providers of managed mode, named as people write them. */

import type { StorageProvider } from './api'

const PROVIDERS: Record<StorageProvider, string> = { ovh: 'OVH', aws: 'AWS', minio: 'MinIO', garage: 'Garage' }

/** Whether a stored value names a provider the web knows. */
export const isProvider = (value: unknown): value is StorageProvider => typeof value === 'string' && value in PROVIDERS

/** A provider's name as people write it; the generic word when there is none. */
export const providerName = (provider: StorageProvider | null): string => (provider ? PROVIDERS[provider] : 'S3')
