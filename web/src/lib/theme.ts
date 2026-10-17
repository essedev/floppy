/*
  Light, dark or the system's choice. design/tokens.css follows prefers-color-scheme and is
  forced by data-theme on <html>; this hook sets or clears that attribute and remembers it.
*/

import { useEffect, useState } from 'react'

/** The theme the person chose. */
export type ThemeMode = 'system' | 'light' | 'dark'

const KEY = 'floppy.theme'

function saved(): ThemeMode {
  try {
    const value = localStorage.getItem(KEY)
    if (value === 'light' || value === 'dark') return value
  } catch {
    // No storage: follow the system.
  }
  return 'system'
}

/** Applies the remembered theme before the first render, so the page does not flash. */
export function applySavedTheme(): void {
  const mode = saved()
  if (mode !== 'system') document.documentElement.dataset.theme = mode
}

/** The chosen theme and a setter that applies and remembers it. */
export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(saved)
  useEffect(() => {
    const root = document.documentElement
    if (mode === 'system') delete root.dataset.theme
    else root.dataset.theme = mode
    try {
      if (mode === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, mode)
    } catch {
      // Applied for this visit only.
    }
  }, [mode])
  return [mode, setMode]
}
