// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { brand } from './src/brand.ts';
import { satteri } from '@astrojs/markdown-satteri';
import { brandTokensPlugin } from './src/lib/brand-tokens.ts';

/** Preload for the two text fonts used above the fold. */
const preload = (/** @type {string} */ href) => ({
	tag: /** @type {const} */ ('link'),
	attrs: { rel: 'preload', href, as: 'font', type: 'font/woff2', crossorigin: '' },
});

// https://astro.build/config
export default defineConfig({
	site: `https://${brand.domain}`,
	markdown: { processor: satteri({ mdastPlugins: [brandTokensPlugin] }) },
	integrations: [
		starlight({
			title: brand.name,
			description: brand.tagline.en,
			defaultLocale: 'root',
			locales: {
				root: { label: 'English', lang: 'en' },
				it: { label: 'Italiano', lang: 'it' },
			},
			social: [{ icon: 'github', label: 'GitHub', href: brand.repo }],
			editLink: { baseUrl: `${brand.repo}/edit/main/site/` },
			favicon: '/favicon.svg',
			// Our own 404 in src/pages: the built-in one has a hero, which would take the landing's.
			disable404Route: true,
			head: [
				preload('/fonts/schibsted-grotesk/schibsted-grotesk-latin-wght-normal.woff2'),
				preload('/fonts/departure-mono/DepartureMono-Regular.woff2'),
			],
			customCss: ['./src/styles/fonts.css', './src/styles/theme.css'],
			components: {
				SiteTitle: './src/components/SiteTitle.astro',
				Hero: './src/components/Hero.astro',
			},
			sidebar: [
				{
					label: 'Start here',
					translations: { it: 'Per iniziare' },
					items: [{ slug: 'introduction' }, { slug: 'install' }, { slug: 'how-it-works' }],
				},
				{
					label: 'Run your own',
					translations: { it: 'In proprio' },
					items: [{ slug: 'self-hosting' }],
				},
				{
					label: 'Help',
					translations: { it: 'Aiuto' },
					items: [{ slug: 'troubleshooting' }, { slug: 'licenses' }],
				},
			],
		}),
	],
});
