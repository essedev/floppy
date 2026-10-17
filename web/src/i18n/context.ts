/* The language context shared by the provider and the hooks. */

import { createContext } from 'react'
import { en } from './en'
import { it, type Dict } from './it'

/** A supported language. */
export type Lang = 'it' | 'en'

/** What the language context holds. */
export interface I18n {
  t: Dict
  lang: Lang
  locale: string
  setLang(lang: Lang): void
}

/** Dictionaries by language. */
export const DICTS: Record<Lang, Dict> = { it, en }

/** The locale Intl formats each language with. */
export const LOCALES: Record<Lang, string> = { it: 'it-IT', en: 'en-GB' }

/** The context; null outside the provider. */
export const I18nContext = createContext<I18n | null>(null)
