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
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
      },
    },
  },
};
