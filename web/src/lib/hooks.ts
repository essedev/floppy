/* Small hooks shared by the features. */

import { useEffect, useState } from 'react'

/** The current time, refreshed every `everyMs`, for running clocks and relative times. */
export function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs)
    return () => clearInterval(timer)
  }, [everyMs])
  return now
}

/** A message that clears itself after `ms`, for confirmations after an action. */
export function useFlash<T>(ms = 6000): [T | null, (value: T | null) => void] {
  const [value, setValue] = useState<T | null>(null)
  useEffect(() => {
    if (value === null) return
    const timer = setTimeout(() => setValue(null), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return [value, setValue]
}
