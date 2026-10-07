# UI guidelines

Short rules for building screens in `frontend/`. Token usage is documented in `frontend/README.md`; this page covers the design intent and the accessibility checks behind it.

## Principles

- **Restrained.** Neutral surfaces, one dark accent, colour reserved for meaning (status, ICP fit, triggers, AI recommendations).
- **Tokens only.** No raw hex values or Tailwind palette colours (`bg-gray-900`, `text-green-700`) in components. Use the semantic utilities (`bg-surface-raised`, `text-fg-muted`, `bg-success-bg`). If a token is missing, add it to `globals.css` and `tailwind.config.ts` for both themes.
- **Never colour alone.** Every status has a label and a glyph in addition to its colour (ICP fit uses 1-3 dots, trigger uses a check or a dash and is filled vs outlined, AI states use `+`, `‖`, `×`).
- **Keyboard first.** Use real `button`/`a` elements, keep the global `:focus-visible` ring (do not remove outlines), and make every new widget operable with Tab, Enter/Space and Escape.
- **Respect user settings.** Colour scheme follows the OS unless the user picks a theme in the header; animation is disabled under `prefers-reduced-motion`.

## Contrast

Targets: WCAG 2.1 AA, i.e. 4.5:1 for text, 3:1 for control borders and the focus ring. `src/lib/tokens.test.ts` reads `globals.css` and asserts every pair below in light and in dark, so a token edit that drops under the target fails `npm test`. Ratios use the WCAG relative-luminance formula (rounded to 0.1).

| Pair (foreground on background)                              | Light              | Dark               | Target |
| ------------------------------------------------------------ | ------------------ | ------------------ | ------ |
| `fg` on `surface` / `surface-raised` / `surface-muted`       | 17.0 / 17.7 / 16.1 | 17.4 / 16.1 / 13.3 | 4.5    |
| `fg-muted` on `surface` / `surface-raised` / `surface-muted` | 7.2 / 7.6 / 6.9    | 7.5 / 7.0 / 5.8    | 4.5    |
| `accent-fg` on `accent` (hover)                              | 17.7 (10.3)        | 16.1 (12.0)        | 4.5    |
| `danger-solid-fg` on `danger-solid` (hover)                  | 6.5 (8.3)          | 4.8 (6.5)          | 4.5    |
| `success`, `icp-strong`: `*-fg` on `*-bg`                    | 6.5, 6.5           | 10.6, 10.6         | 4.5    |
| `warning`, `icp-medium`                                      | 6.4, 6.4           | 10.4, 10.4         | 4.5    |
| `danger`, `info`                                             | 6.8, 7.1           | 8.5, 8.1           | 4.5    |
| `icp-weak`, `ai-skip`                                        | 8.3, 8.3           | 10.0, 10.0         | 4.5    |
| `trigger-yes`, `trigger-no`                                  | 8.1, 7.6           | 8.0, 7.0           | 4.5    |
| `ai-add`, `ai-hold`                                          | 6.7, 6.6           | 9.8, 8.3           | 4.5    |
| `line-strong` on `surface` / `surface-raised`                | 4.6 / 4.8          | 4.0 / 3.7          | 3      |
| `ring` on `surface` / `surface-raised`                       | 4.9 / 5.2          | 7.5 / 7.0          | 3      |

`line` (the hairline card/table border) is decorative and intentionally lighter than 3:1; anything that identifies an interactive control (outline button, trigger "No" pill) uses `line-strong`.

## Adding a component

1. Put it in `src/components/ui/` with a `Name.test.tsx` (query by role, assert behaviour).
2. Style with token utilities only; add variants as a `Record<Variant, string>` of full class names (so Tailwind can see them).
3. Check it in both themes and at 390px width.
