# Website

The product's public site: a landing page and the docs, in English (default) and Italian. Built with [Astro](https://astro.build) and [Starlight](https://starlight.astro.build), managed with pnpm.

```sh
pnpm install
pnpm dev        # http://localhost:4321
pnpm build      # static site in dist/
pnpm preview    # serve dist/
pnpm check      # astro check (types and templates)
```

## Where things live

- `src/brand.ts`: name, tagline, repository and domain. A rename changes `name` there; the header, landing, install line and the doc pages all read from it.
- Doc pages (`src/content/docs/`, Italian under `it/`) never spell the name out: they use `%name%`, `%slug%`, `%volume%`, `%cask%`, `%repo%`, `%domain%`, filled at build time by `src/lib/brand-tokens.ts`. An unknown token fails the build. Frontmatter is not filled, so titles and descriptions avoid the name.
- `src/i18n/landing.ts`: every string of the landing, in both languages. Its numbers come from `docs/ARCHITECTURE.md` and `docs/CYCLES.md`: change them there first.
- `src/components/`: the landing (`Landing.astro`, `Hero.astro`, `Faceplate.astro`, `Diagram.astro`) and two overrides of Starlight's header: the title (`SiteTitle.astro`) and a docs link before the social icons (`SocialIcons.astro`).
- `src/styles/tokens.css`: a copy of `design/tokens.css`, the source of every colour and of the detail set (LED in its bezel, readout, the slot as list marker). Change a value there, then copy the file here again, without its Google Fonts import.
- `src/styles/theme.css`: those tokens mapped onto Starlight's custom properties.
- `ec.config.mjs`: code block styling, in its own file because Expressive Code needs it serializable.
- `public/fonts/`: Schibsted Grotesk and Departure Mono, self-hosted, each with its SIL Open Font License. Departure Mono is the official v1.500 release, unchanged (source and checksum in `src/styles/fonts.css`).

The hero faceplate plays the panel's states in a loop (connected, a project downloading, connected, the project freed); with `prefers-reduced-motion` it stays on Connected.

After changing `ec.config.mjs` or the Markdown plugin, delete `.astro/` and `node_modules/.astro/`: rendered Markdown is cached there and will not pick up the change.
