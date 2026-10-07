export type ThemePreference = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "abm-theme";

/**
 * Inline script run before first paint (see layout.tsx) so a stored choice is
 * applied without a flash of the wrong theme.
 */
export const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;
