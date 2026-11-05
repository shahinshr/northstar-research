import type { Config } from "tailwindcss";

const v = (n: string) => `var(--${n})`;

export default {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        page: v("page"),
        surface: v("surface"),
        sunken: v("sunken"),
        raised: v("raised"),
        ink: v("ink"),
        ink2: v("ink-2"),
        muted: v("muted"),
        line: v("line"),
        "line-strong": v("line-strong"),
        grid: v("grid"),
        accent: v("accent"),
        "accent-ink": v("accent-ink"),
        good: v("good"),
        warn: v("warn"),
        serious: v("serious"),
        bad: v("bad"),
        up: v("up"),
        down: v("down"),
      },
      fontFamily: { sans: ["var(--font-inter)", "system-ui", "-apple-system", "Segoe UI", "sans-serif"] },
      fontSize: { "2xs": ["0.6875rem", "1rem"] },
    },
  },
  plugins: [],
} satisfies Config;
