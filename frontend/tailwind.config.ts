import type { Config } from "tailwindcss";

/**
 * AIMO PROTOCOL design tokens — premium light theme.
 *
 * The app's components are written against semantic scales, so the theme lives here:
 *   ink-*      surfaces & borders  (950 = raised white surface … 900 = canvas … 700 = hairline border … 500 = strong border)
 *   mist-*     text                (100 = primary … 400 = secondary … 500 = tertiary; all ≥ 4.5:1 on white)
 *   teal-*     the single brand accent (indigo). Kept under this name so existing components inherit it.
 *   electric-* alias of the accent (charts, focus)
 *   navy-*     sidebar / emphasis surfaces
 * Status text shades (emerald/amber/rose/violet/blue 50–300) are remapped to dark, AA-contrast variants because the
 * components use them for text on tinted backgrounds; the 400–600 shades stay Tailwind defaults for tints and dots.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#FFFFFF",
          900: "#F7F8FA",
          850: "#FFFFFF",
          800: "#F2F4F7",
          750: "#EAECF0",
          700: "#E4E7EC",
          600: "#D0D5DD",
          500: "#98A2B3",
        },
        mist: { 100: "#101828", 200: "#1D2939", 300: "#344054", 400: "#475467", 500: "#5D6679" },
        teal: { 50: "#312E81", 100: "#3730A3", 200: "#3730A3", 300: "#4338CA", 400: "#4F46E5", 500: "#4F46E5", 600: "#4338CA" },
        electric: { 400: "#6366F1", 500: "#4F46E5", 600: "#4338CA" },
        navy: { 950: "#0A101F", 900: "#0E1628", 800: "#151F35", 700: "#1E2A45", 600: "#2C3A5A", 300: "#9AA6C2", 200: "#C5CDE0", 100: "#E7EBF4" },
        emerald: { 50: "#054F31", 100: "#05603A", 200: "#067647", 300: "#067647" },
        amber: { 50: "#7A2E0E", 100: "#93370D", 200: "#93370D", 300: "#B54708" },
        rose: { 50: "#7A271A", 100: "#912018", 200: "#B42318", 300: "#B42318" },
        violet: { 100: "#5B21B6", 200: "#6D28D9" },
        blue: { 100: "#3538CD", 200: "#3538CD" },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["11px", "16px"],
      },
      boxShadow: {
        glow: "0 1px 2px rgba(16,24,40,0.06)",
        card: "0 1px 2px rgba(16,24,40,0.04)",
        pop: "0 12px 32px -8px rgba(16,24,40,0.18), 0 2px 6px rgba(16,24,40,0.06)",
      },
      keyframes: {
        fadein: { from: { opacity: "0", transform: "translateY(2px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        pulseline: { "0%,100%": { opacity: "0.45" }, "50%": { opacity: "1" } },
        shimmer: { "0%": { backgroundPosition: "-400px 0" }, "100%": { backgroundPosition: "400px 0" } },
      },
      animation: {
        fadein: "fadein .2s ease-out both",
        pulseline: "pulseline 1.6s ease-in-out infinite",
        shimmer: "shimmer 1.4s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
