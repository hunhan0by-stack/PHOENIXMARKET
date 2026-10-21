/**
 * Tailwind build for static/css/app.css (Tailwind v3 standalone CLI, no npm needed).
 * After changing classes in templates or static/js, rebuild — see "Styles" in README.md.
 */
module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        brandBg: "#020617",
        brandSurface: "#020617",
        brandBorder: "#1f2937",
        brandRed: "#ef4444",
        brandOrange: "#f97316",
        // Slightly lighter than Tailwind's #64748b so small grey text reaches
        // WCAG AA contrast (4.5:1) on the dark backgrounds (5.2:1 on slate-950).
        slate: { 500: "#748299" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
      },
    },
  },
};
