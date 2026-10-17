// Strings of the site's header controls (theme switch, language selector, docs link, menu
// button), shared by the landing's own header and Starlight's on the docs pages.

import type { Lang } from '@/brand';

/** Every string the header controls show, for one language. */
export interface ChromeStrings {
  /** Accessible name of the theme switch. */
  themeLabel: string;
  /** Readouts under the theme switch, one per position. */
  theme: { light: string; auto: string; dark: string };
  /** Accessible names of the positions, spelled out. */
  themeNames: { light: string; auto: string; dark: string };
  /** Accessible name of the language selector. */
  languageLabel: string;
  /** Name of the language, in that language, for screen readers. */
  languageName: string;
  /** Header link to the docs. */
  docs: string;
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
    themeLabel: 'Theme',
    theme: { light: 'Light', auto: 'Auto', dark: 'Dark' },
    themeNames: { light: 'Light', auto: 'Same as the system', dark: 'Dark' },
    languageLabel: 'Language',
    languageName: 'English',
    docs: 'Docs',
    menu: 'Menu',
    nav: 'Main',
    skip: 'Skip to content',
  },
  it: {
    themeLabel: 'Tema',
    theme: { light: 'Chiaro', auto: 'Auto', dark: 'Scuro' },
    themeNames: { light: 'Chiaro', auto: 'Come il sistema', dark: 'Scuro' },
    languageLabel: 'Lingua',
    languageName: 'Italiano',
    docs: 'Documentazione',
    menu: 'Menu',
    nav: 'Principale',
    skip: 'Vai al contenuto',
  },
};
