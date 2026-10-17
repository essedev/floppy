/* The UI text for an API error, from the dictionary by its code. */

import { useI18n } from '@/i18n'
import { isApiError } from '@/lib/api'

/** A function from an error to the sentence the UI shows for it. */
export function useErrorText(): (error: unknown) => string {
  const { t } = useI18n()
  return (error) => (isApiError(error) ? t.errors[error.code] : t.errors.server)
}
