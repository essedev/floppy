// Sätteri mdast plugin (Astro 7's default Markdown processor) that fills `%name%`, `%slug%`,
// `%cask%`, ... in the doc pages from `brand.ts`, so a rename does not touch the Markdown.
// It runs on text, inline code, code blocks and link URLs; frontmatter is not Markdown, so
// page titles and descriptions avoid the name.

// Relative import: astro.config.mjs loads this file, where the @/ alias does not resolve.
import { defineMdastPlugin } from 'satteri';
import { brandTokens } from '../brand';

const TOKEN = /%([a-z]+)%/g;

function fill(input: string, file: URL | undefined): string {
  return input.replace(TOKEN, (match, key: string) => {
    const value = brandTokens[key];
    // A typo would otherwise ship as literal text: fail the build instead.
    if (value === undefined) throw new Error(`Unknown brand token ${match} in ${file ?? 'a doc page'}`);
    return value;
  });
}

/** The plugin: replaces brand tokens across the Markdown tree. */
export const brandTokensPlugin = defineMdastPlugin({
  name: 'brand-tokens',
  text(node, ctx) {
    if (node.value.includes('%')) ctx.setProperty(node, 'value', fill(node.value, ctx.fileURL));
  },
  inlineCode(node, ctx) {
    if (node.value.includes('%')) ctx.setProperty(node, 'value', fill(node.value, ctx.fileURL));
  },
  code(node, ctx) {
    if (node.value.includes('%')) ctx.setProperty(node, 'value', fill(node.value, ctx.fileURL));
  },
  link(node, ctx) {
    if (node.url.includes('%')) ctx.setProperty(node, 'url', fill(node.url, ctx.fileURL));
  },
});
