import type { Config } from "tailwindcss";

/** Colour utilities map to CSS variables defined in src/app/globals.css. */
const v = (name: string) => `var(--${name})`;

/** Soft (tinted background + readable foreground) semantic pair. */
const soft = (name: string) => ({
  DEFAULT: v(`${name}-fg`),
  bg: v(`${name}-bg`),
  fg: v(`${name}-fg`),
});

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: v("surface"),
          raised: v("surface-raised"),
          muted: v("surface-muted"),
        },
        line: { DEFAULT: v("line"), strong: v("line-strong") },
        fg: { DEFAULT: v("fg"), muted: v("fg-muted") },
        accent: {
          DEFAULT: v("accent"),
          hover: v("accent-hover"),
          fg: v("accent-fg"),
        },
        ring: v("ring"),
        overlay: v("overlay"),
        success: soft("success"),
        warning: soft("warning"),
        danger: {
          ...soft("danger"),
          solid: v("danger-solid"),
          "solid-hover": v("danger-solid-hover"),
          "solid-fg": v("danger-solid-fg"),
        },
        info: soft("info"),
        icp: {
          strong: soft("icp-strong"),
          medium: soft("icp-medium"),
          weak: soft("icp-weak"),
        },
        trigger: { yes: soft("trigger-yes"), no: soft("trigger-no") },
        ai: {
          add: soft("ai-add"),
          hold: soft("ai-hold"),
          skip: soft("ai-skip"),
        },
      },
      spacing: {
        gutter: v("space-gutter"),
        sidebar: v("size-sidebar"),
        header: v("size-header"),
      },
      borderRadius: {
        sm: v("radius-sm"),
        md: v("radius-md"),
        lg: v("radius-lg"),
      },
      boxShadow: { sm: v("shadow-sm"), md: v("shadow-md") },
      fontFamily: { sans: v("font-sans"), mono: v("font-mono") },
      fontSize: {
        xs: [v("text-xs"), { lineHeight: v("leading-xs") }],
        sm: [v("text-sm"), { lineHeight: v("leading-sm") }],
        base: [v("text-base"), { lineHeight: v("leading-base") }],
        lg: [v("text-lg"), { lineHeight: v("leading-lg") }],
        xl: [v("text-xl"), { lineHeight: v("leading-xl") }],
        "2xl": [v("text-2xl"), { lineHeight: v("leading-2xl") }],
      },
    },
  },
  plugins: [],
};

export default config;
