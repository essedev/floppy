/*
  The language of the UI. Components read strings with useI18n().t and fill placeholders with
  fmt, rich and plural; the provider is in I18nProvider.tsx.
*/

import { Fragment, useContext, type ReactNode } from 'react'
import { I18nContext, type I18n } from './context'
import type { Plural } from './it'

export type { Lang } from './context'

/** The dictionary and language of the UI. */
export function useI18n(): I18n {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n outside I18nProvider')
  return value
}

const TOKEN = /\{(\w+)\}/g

/** A template with its {placeholders} filled as text. */
export function fmt(template: string, values: Record<string, string | number>): string {
  return template.replace(TOKEN, (match, key: string) => (key in values ? String(values[key]) : match))
}

/** A template with its {placeholders} filled with elements, e.g. a name in bold. */
export function rich(template: string, values: Record<string, ReactNode>): ReactNode {
  // split with a capture group alternates text and placeholder names.
  const parts = template.split(TOKEN)
  return parts.map((part, i) => <Fragment key={i}>{i % 2 === 1 ? (values[part] ?? `{${part}}`) : part}</Fragment>)
}

/** The singular or plural form for n, with {n} filled. */
export function plural(forms: Plural, n: number, locale: string, values: Record<string, string | number> = {}): string {
  const form = new Intl.PluralRules(locale).select(n) === 'one' ? forms.one : forms.other
  return fmt(form, { n: new Intl.NumberFormat(locale).format(n), ...values })
}
