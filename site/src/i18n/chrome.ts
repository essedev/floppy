// Strings of the site's header controls (theme switch, language selector, docs and sign-in
// links, menu button), shared by the landing's own header and Starlight's on the docs pages.

import type { Lang } from '@/brand';

/** Every string the header controls show, for one language. */
export interface ChromeStrings {
  /** Accessible name of the theme switch, which is on when the theme is dark. */
  themeLabel: string;
  /** Readouts under the theme switch, one per position. */
  theme: { light: string; dark: string };
  /** Accessible name of the language selector. */
  languageLabel: string;
  /** Name of the language, in that language, for screen readers. */
  languageName: string;
  /** Header link to the docs. */
  docs: string;
  /** Header link to the hosted service's admin web. */
  signIn: string;
  /** Accessible name of the docs pages' menu button. */
  menu: string;
  /** The landing's skip link. */
  skip: string;
  /** Accessible name of the landing's header navigation. */
  nav: string;
}

/** Header strings by language. */
export const chrome: Record<Lang, ChromeStrings> = {
  en: {
    themeLabel: 'Dark theme',
    theme: { light: 'Light', dark: 'Dark' },
    languageLabel: 'Language',
    languageName: 'English',
    docs: 'Docs',
    signIn: 'Sign in',
    menu: 'Menu',
    nav: 'Main',
    skip: 'Skip to content',
  },
  it: {
    themeLabel: 'Tema scuro',
    theme: { light: 'Chiaro', dark: 'Scuro' },
    languageLabel: 'Lingua',
    languageName: 'Italiano',
    docs: 'Documentazione',
    signIn: 'Accedi',
    menu: 'Menu',
    nav: 'Principale',
    skip: 'Vai al contenuto',
  },
};
