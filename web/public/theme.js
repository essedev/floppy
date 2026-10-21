// Applies the stored theme before the first paint: a blocking script in <head>, a file because
// the CSP allows no inline script. With no stored choice it does nothing and design/tokens.css
// follows the system. The key and the values are those of src/lib/theme.ts.
try {
  var theme = localStorage.getItem('floppy.theme')
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme
} catch (e) {
  // No storage: the system decides.
}
