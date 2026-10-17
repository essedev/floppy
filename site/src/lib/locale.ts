// Language helpers for the custom pages: which dictionary a route uses and how its links are
// prefixed. English is the root locale, Italian lives under /it/.

import type { Lang } from '@/brand';

/** The site language for a BCP-47 tag from Starlight's route data. */
export function langOf(tag: string | undefined): Lang {
  return tag?.toLowerCase().startsWith('it') ? 'it' : 'en';
}

/** A site path in the given language, e.g. `/install/` becomes `/it/install/` in Italian. */
export function localePath(lang: Lang, path: string): string {
  return lang === 'en' ? path : `/${lang}${path}`;
}
