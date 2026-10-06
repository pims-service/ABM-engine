import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Vitest runs from the frontend/ root (jsdom's import.meta.url is not a file URL).
const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");

/** Returns the `--name: value` declarations inside the block opened by `selector`. */
function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`Selector not found: ${selector}`);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const vars: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    const [, name, value] = m;
    if (name && value) vars[name] = value.trim();
  }
  return vars;
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const SOFT = [
  "success",
  "warning",
  "danger",
  "info",
  "icp-strong",
  "icp-medium",
  "icp-weak",
  "trigger-yes",
  "trigger-no",
  "ai-add",
  "ai-hold",
  "ai-skip",
];

const themes = {
  light: block(":root"),
  "dark (prefers-color-scheme)": block(':root:not([data-theme="light"])'),
  'dark (data-theme="dark")': block(':root[data-theme="dark"]'),
};

describe("design tokens", () => {
  it("keeps the two dark blocks identical", () => {
    expect(themes["dark (prefers-color-scheme)"]).toEqual(
      themes['dark (data-theme="dark")'],
    );
  });

  it("defines every themed colour in both light and dark", () => {
    const light = themes.light;
    const dark = themes['dark (data-theme="dark")'];
    for (const name of Object.keys(dark)) {
      expect(light, `--${name} missing in light`).toHaveProperty(name);
    }
    for (const name of SOFT) {
      expect(dark).toHaveProperty(`${name}-bg`);
      expect(dark).toHaveProperty(`${name}-fg`);
    }
  });

  describe.each(Object.entries(themes))("contrast, %s", (_name, t) => {
    const hex = (key: string) => {
      const value = t[key] ?? "";
      if (!/^#[0-9a-f]{6}$/i.test(value)) {
        throw new Error(`--${key} is not a 6-digit hex colour: ${value}`);
      }
      return value;
    };
    const surfaces = ["surface", "surface-raised", "surface-muted"];

    it.each(surfaces)("body and muted text reach 4.5:1 on --%s", (s) => {
      expect(contrast(hex("fg"), hex(s))).toBeGreaterThanOrEqual(4.5);
      expect(contrast(hex("fg-muted"), hex(s))).toBeGreaterThanOrEqual(4.5);
    });

    it.each(SOFT)("--%s foreground reaches 4.5:1 on its background", (n) => {
      expect(contrast(hex(`${n}-fg`), hex(`${n}-bg`))).toBeGreaterThanOrEqual(
        4.5,
      );
    });

    it("solid buttons reach 4.5:1 including hover", () => {
      const pairs: Array<[string, string]> = [
        ["accent-fg", "accent"],
        ["accent-fg", "accent-hover"],
        ["danger-solid-fg", "danger-solid"],
        ["danger-solid-fg", "danger-solid-hover"],
      ];
      for (const [fg, bg] of pairs) {
        expect(contrast(hex(fg), hex(bg))).toBeGreaterThanOrEqual(4.5);
      }
    });

    it("control borders and the focus ring reach 3:1 on page and card", () => {
      for (const s of ["surface", "surface-raised"]) {
        expect(contrast(hex("line-strong"), hex(s))).toBeGreaterThanOrEqual(3);
        expect(contrast(hex("ring"), hex(s))).toBeGreaterThanOrEqual(3);
      }
    });
  });
});
