// The product's identity in one place (name chosen in DECISIONS #14). A rename changes
// `name` here, plus `repo` and `domain` if those move too. Everything else
// on the site (title, header, landing, install line, doc pages through the %tokens%) reads
// from this file.

const name = 'Floppy';
const slug = name.toLowerCase();

/** Name, derived forms, links and tagline of the product. */
export const brand = {
  /** Product name, as the Finder shows the disk. */
  name,
  /** Lowercase form: the command, the Homebrew cask, the wordmark on the faceplate. */
  slug,
  /** The disk's mount point on the Mac. */
  volume: `/Volumes/${name}`,
  /** Homebrew cask. TODO: the tap is not published yet; the final form may be `<tap>/<cask>`. */
  cask: slug,
  /** Homebrew tap the cask lives in, as concept-d-onboarding draws it (DECISIONS #10). */
  tap: 'essedev/tap',
  /** What `brew install --cask` takes: the cask through its tap. */
  caskRef: `essedev/tap/${slug}`,
  repo: 'https://github.com/essedev/fluix',
  /** TODO: the domain depends on the final name. `.example` is reserved and resolves nowhere. */
  domain: 'floppy.example',
  tagline: {
    en: 'A shared disk for video teams, right in the Finder.',
    it: 'Un disco condiviso per chi monta video, direttamente nel Finder.',
  },
} as const;

/** The site's languages: English is the default, Italian the second locale. */
export type Lang = keyof typeof brand.tagline;

/**
 * Values the doc pages can reference as `%name%`, `%slug%`, ... (see `lib/brand-tokens.ts`),
 * so Markdown never spells the product name out.
 */
export const brandTokens: Record<string, string> = {
  name: brand.name,
  slug: brand.slug,
  volume: brand.volume,
  cask: brand.caskRef,
  repo: brand.repo,
  domain: brand.domain,
};
