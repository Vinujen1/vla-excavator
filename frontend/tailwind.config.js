/** @type {import('tailwindcss').Config} */
// "Site sheet" theme: cool concrete paper, white work sheets, graphite ink.
// Colour roles are semantic and never decorative:
//   machine (yellow) = real machine activity · accent (plan blue) = controls and anything simulated/drawn
//   fault (signal red) = stop and faults · ok / warn = status
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#EEF1F4",
        sheet: "#FFFFFF",
        graphite: "#1C2430",
        muted: "#566173",
        faint: "#8792A2",
        rule: "#D5DBE2",
        accent: { DEFAULT: "#2F5EA8", dim: "#244A86", wash: "#E8EEF8" },
        machine: { DEFAULT: "#F2B300", ink: "#7A5A00", wash: "#FFF6D9" },
        fault: { DEFAULT: "#D62F22", wash: "#FCEBEA" },
        ok: { DEFAULT: "#23824F", wash: "#E7F4EC" },
        warn: { DEFAULT: "#B45309", wash: "#FDF3E4" },
      },
      fontFamily: {
        sans: ["Barlow", "ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        display: ["'Barlow Semi Condensed'", "Barlow", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: { "2xs": ["11px", "15px"] },
    },
  },
  plugins: [],
};
