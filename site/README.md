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
- `src/i18n/landing.ts`: every string of the landing, in both languages.
- `src/components/`: the landing (`Landing.astro`, `Hero.astro`, `Faceplate.astro`, `Diagram.astro`) and the header title (`SiteTitle.astro`), which overrides Starlight's.
- `src/styles/theme.css`: the Grafite identity mapped onto Starlight's custom properties, plus the detail vocabulary (LED in its bezel, readouts, the slot as list marker).
- `ec.config.mjs`: code block styling, in its own file because the landing's `<Code>` needs it serializable.
- `public/fonts/`: Schibsted Grotesk and Departure Mono, self-hosted, each with its SIL Open Font License.

After changing `ec.config.mjs` or the Markdown plugin, delete `.astro/` and `node_modules/.astro/`: rendered Markdown is cached there and will not pick up the change.
