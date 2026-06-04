import type { Config } from "tailwindcss";

// Dark-mode-first design system. Tokens are intentionally restrained:
// a deep neutral surface scale plus a single signal accent and semantic
// status colors. This yields the observability/AI-infra aesthetic without
// the rainbow look of generic admin templates.
const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Surface scale (near-black to elevated panels)
        surface: {
          0: "#06080c",
          50: "#0a0e15",
          100: "#0f141d",
          200: "#161c28",
          300: "#1e2633",
          400: "#2a3342",
        },
        border: {
          subtle: "#1c2431",
          DEFAULT: "#26303f",
          strong: "#36425a",
        },
        content: {
          primary: "#e6edf6",
          secondary: "#9aa7b8",
          muted: "#5c6b80",
        },
        accent: {
          DEFAULT: "#3da9fc",
          muted: "#1f6fb2",
          glow: "#5fc1ff",
        },
        status: {
          live: "#22d39a",
          idle: "#8a93a3",
          warn: "#f5b14c",
          error: "#f4607a",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
        glow: "0 0 0 1px rgba(61,169,252,0.25), 0 0 24px -6px rgba(61,169,252,0.45)",
      },
      keyframes: {
        "pulse-live": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
      },
      animation: {
        "pulse-live": "pulse-live 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
