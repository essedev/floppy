// The product's identity in one place (name chosen in DECISIONS #14). A rename changes
// `name` here, plus `repo` and `domain` if those move too. Everything else
// on the site (title, header, landing, install line, doc pages through the %tokens%) reads
// from this file.

const name = 'Floppy';
const slug = name.toLowerCase();
/** TODO: the domain depends on the final name. `.example` is reserved and resolves nowhere. */
const domain = 'floppy.example';

/** Name, derived forms, links and tagline of the product. */
export const brand = {
  /** Product name, as the Finder shows the disk. */
  name,
  /** Lowercase form: the command, the Homebrew cask, the wordmark on the faceplate. */
  slug,
  /** The disk's mount point on the Mac. */
  volume: `/Volumes/${name}`,
  /** Homebrew cask (source in packaging/homebrew/Casks). */
  cask: slug,
  /** Homebrew tap the cask lives in, the GitHub repo essedev/homebrew-floppy (DECISIONS #10). */
  tap: `essedev/${slug}`,
  /** What `brew install --cask` takes: the fully qualified name, which also
   *  grants Homebrew's tap trust to this cask alone. */
  caskRef: `essedev/${slug}/${slug}`,
  repo: 'https://github.com/essedev/floppy',
  domain,
  /** The hosted service's admin web (web/), where the header's sign-in link goes. A
   *  self-hosted team signs in on its own instance's address instead. */
  dashboard: `https://app.${domain}`,
  tagline: {
    en: 'A shared disk for big files, right in the Finder.',
    it: 'Un disco condiviso per file grandi, direttamente nel Finder.',
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
