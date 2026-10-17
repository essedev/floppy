// @ts-check
// Expressive Code options for Starlight's code blocks, in their own file because the
// `<Code>` component on the landing needs them serializable. Frames take the drive's
// surfaces: raised putty in light, sunken graphite in dark; no shadow.
import { defineEcConfig } from '@astrojs/starlight/expressive-code';

/** A value per theme type. */
const byTheme = (/** @type {string} */ dark, /** @type {string} */ light) =>
	/** @param {{ theme: { type: string } }} ctx */
	({ theme }) => (theme.type === 'dark' ? dark : light);

export default defineEcConfig({
	themes: ['github-dark-default', 'github-light-default'],
	// A plain frame for every block: the terminal chrome (three dots) is generic decoration.
	defaultProps: { frame: 'code' },
	styleOverrides: {
		borderRadius: '10px',
		borderColor: 'var(--fx-line)',
		codeFontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
		uiFontFamily: 'var(--__sl-font)',
		codeBackground: byTheme('#161514', '#fbfaf7'),
		frames: {
			editorTabBarBackground: byTheme('#1d1c1a', '#ebe8e1'),
			editorActiveTabBackground: byTheme('#161514', '#fbfaf7'),
			terminalTitlebarBackground: byTheme('#1d1c1a', '#ebe8e1'),
			terminalBackground: byTheme('#161514', '#fbfaf7'),
			frameBoxShadowCssValue: 'none',
		},
	},
});
