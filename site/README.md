# Website

The product's public site: a landing page and the docs, in English (default) and Italian. Built with [Astro](https://astro.build); the docs use [Starlight](https://starlight.astro.build) for the sidebar, search and translations, the landing is a plain Astro page. Managed with pnpm.

**The site describes the finished product.** It goes online when the product is complete: no "coming soon", no status notes, no dates on the measures. The current state lives in the repository's README and `docs/ROADMAP.md`.

```sh
pnpm install
pnpm dev        # http://localhost:4321
pnpm build      # static site in dist/
pnpm preview    # serve dist/
pnpm check      # astro check (types and templates)
```

## Where things live

- `src/brand.ts`: name, tagline, repository, domain, cask and tap. A rename changes `name` there; the header, landing, install line and the doc pages all read from it.
- Doc pages (`src/content/docs/`, Italian under `it/`) never spell the name out: they use `%name%`, `%slug%`, `%volume%`, `%cask%` (the cask through its tap), `%repo%`, `%domain%`, filled at build time by `src/lib/brand-tokens.ts`. An unknown token fails the build. Frontmatter is not filled, so titles and descriptions avoid the name.
- `src/i18n/landing.ts`: every string of the landing, in both languages. Its numbers come from `docs/ARCHITECTURE.md`: change them there first. `src/i18n/chrome.ts`: the header controls' strings.
- `src/pages/index.astro`, `src/pages/it/index.astro`: the landing, on its own layout (`src/layouts/LandingLayout.astro`) with `Landing.astro`, `Hero.astro`, `Faceplate.astro` and `Diagram.astro`.
- `src/components/chrome/`: the header controls both the landing and the docs use: the wordmark, the theme switch (a three-position slot, light, auto, dark, stored under Starlight's `starlight-theme` key) and the language selector. The Starlight overrides (`SiteTitle`, `ThemeSelect`, `LanguageSelect`, `SocialIcons`, `Search`, `MobileMenuToggle` in `src/components/`) wrap them or restyle Starlight's own.
- `src/styles/tokens.css`: a copy of `design/tokens.css`, the source of every colour and of the detail set (LED in its bezel, readout, the slot as list marker). Change a value there, then copy the file here again, without its Google Fonts import.
- `src/styles/theme.css`: those tokens mapped onto Starlight's custom properties, also loaded by the landing.
- `ec.config.mjs`: code block styling, in its own file because Expressive Code needs it serializable.
- `public/fonts/`: Schibsted Grotesk and Departure Mono, self-hosted, each with its SIL Open Font License. Departure Mono is the official v1.500 release, unchanged (source and checksum in `src/styles/fonts.css`).

The hero faceplate plays the panel's states in a loop (connected, a project downloading, connected, the project freed); with `prefers-reduced-motion` it stays on Connected.

After changing `ec.config.mjs` or the Markdown plugin, delete `.astro/` and `node_modules/.astro/`: rendered Markdown is cached there and will not pick up the change.
