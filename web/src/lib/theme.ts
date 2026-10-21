/*
  Light or dark. Until the person chooses, the page is in the system's scheme and follows it
  live: design/tokens.css reads prefers-color-scheme while <html> has no data-theme. Choosing
  stores the theme and sets data-theme, and from then on the system no longer drives it.
  public/theme.js applies the stored choice before the first paint, under the same key.
*/

import { useCallback, useEffect, useState } from 'react'

/** The theme on screen. */
export type Theme = 'light' | 'dark'

const KEY = 'floppy.theme'
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')

/** The choice made in this visit, kept when storage refuses it (private mode). */
let chosen: Theme | null = null

/** The choice the person made, or null while the system decides. */
function storedTheme(): Theme | null {
  if (chosen) return chosen
  try {
    const value = localStorage.getItem(KEY)
    if (value === 'light' || value === 'dark') return value
  } catch {
    // No storage: the system decides.
  }
  return null
}

const systemTheme = (): Theme => (darkQuery.matches ? 'dark' : 'light')

/** The theme on screen and a setter that applies and remembers the person's choice. */
export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? systemTheme())

  // Until the person chooses, follow the system as it changes (the tokens already do).
  useEffect(() => {
    const follow = () => {
      if (!storedTheme()) setTheme(systemTheme())
    }
    darkQuery.addEventListener('change', follow)
    return () => darkQuery.removeEventListener('change', follow)
  }, [])

  const choose = useCallback((next: Theme) => {
    chosen = next
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem(KEY, next)
    } catch {
      // Applied for this visit only.
    }
    setTheme(next)
  }, [])

  return [theme, choose]
}
