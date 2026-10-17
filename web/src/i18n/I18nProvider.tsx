/* Provides the dictionary of the chosen language: Italian by default, remembered per browser. */

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { DICTS, I18nContext, LOCALES, type I18n, type Lang } from './context'

const LANG_KEY = 'floppy.lang'

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_KEY)
    if (saved === 'it' || saved === 'en') return saved
  } catch {
    // No storage: fall back to Italian.
  }
  return 'it'
}

/** The language provider. */
export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang)
  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])
  const value = useMemo<I18n>(
    () => ({
      t: DICTS[lang],
      lang,
      locale: LOCALES[lang],
      setLang(next) {
        setLangState(next)
        try {
          localStorage.setItem(LANG_KEY, next)
        } catch {
          // Not remembered across visits: the choice still applies now.
        }
      },
    }),
    [lang],
  )
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
