/**
 * Visual theme selection (2026-09-25) — one reactive singleton.
 *
 * Why this is not a database setting
 * ----------------------------------
 * Same reasoning as `app/model_catalog.py` made for the model picker: a
 * theme is a *preference of this browser*, not teaching data. It never
 * enters a grade, never appears in a class statistic, and there is no
 * reason one student's choice should be visible to anyone else. Putting
 * it in the database would mean a migration, an endpoint, and a
 * permission question ("can a teacher change a student's theme?") whose
 * honest answer is "why would they".
 *
 * So: `localStorage`, applied to `<html>` as `data-theme`, and the CSS
 * in `assets/themes.css` does the rest.
 *
 * Why `themeVersion` exists separately from `theme`
 * -------------------------------------------------
 * Two consumers cannot read a CSS variable reactively, because CSS
 * variables are not reactive:
 *
 * * **Monaco** needs `defineTheme` called again with new literals —
 *   `monaco-theme.ts` reads tokens once and hands Monaco hex strings.
 * * **ECharts** needs its palette rebuilt — `RadarChart.vue` caches the
 *   resolved palette in a module variable for exactly the reason that
 *   makes this awkward (`getComputedStyle` is not cheap, and it caches
 *   deliberately).
 *
 * Both therefore watch a counter. A counter rather than the theme id
 * because "switch from A to B and back to A" is still a change that both
 * must react to — the id would compare equal and nothing would happen.
 *
 * Why an unknown stored value is ignored rather than honoured
 * -----------------------------------------------------------
 * A theme name from a future version, or one someone typed into
 * devtools, would otherwise leave `data-theme` set to a value no rule
 * matches — which silently renders the *default* theme while the UI
 * claims something else. Falling back is the same decision
 * `model_catalog.active_provider()` makes, for the same reason.
 */

import { computed, ref } from 'vue'

/** The themes a user can pick. `neon` is the default in `tokens.css`. */
export type ThemeId = 'neon' | 'terminal' | 'paper'

export interface ThemeOption {
  id: ThemeId
  /** What the picker shows. */
  label: string
  /** One line on what the theme is *for*, since the names alone don't say. */
  hint: string
}

/**
 * The themes, in the order the picker shows them.
 *
 * Display copy lives here rather than in the component because it is
 * content: adding a theme should be one edit to this list plus one block
 * in `themes.css`, not a component that needs to learn a fourth name.
 */
export const THEMES: readonly ThemeOption[] = [
  {
    id: 'neon',
    label: '霓虹深空',
    hint: '深色玻璃质感，带辉光。默认。'
  },
  {
    id: 'terminal',
    label: '赛制终端',
    hint: '近黑、直角、琥珀金。像评测机，干扰最少，最适合比赛。'
  },
  {
    id: 'paper',
    label: '纸感学术',
    hint: '暖白纸张，衬线标题。适合投屏、打印和光线强的教室。'
  }
] as const

const DEFAULT_THEME: ThemeId = 'neon'
const STORAGE_KEY = 'algoforge.theme'

/** The ids this build actually ships CSS for. */
const THEME_IDS: readonly string[] = THEMES.map((t) => t.id)

function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && THEME_IDS.includes(value)
}

function readStoredTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return isThemeId(stored) ? stored : DEFAULT_THEME
  } catch {
    // Private-mode Safari and locked-down Electron contexts throw on
    // access rather than returning null. Defaulting is the right
    // degradation; breaking the boot is not.
    return DEFAULT_THEME
  }
}

/** The active theme. Written only through {@link setTheme}. */
export const theme = ref<ThemeId>(readStoredTheme())

/**
 * Bumped on every change, including "back to the same-looking value".
 * See the module docstring for what watches it and why.
 */
export const themeVersion = ref(0)

/** The active theme's display copy, so no component has to look it up. */
export const themeOption = computed<ThemeOption>(
  () => THEMES.find((t) => t.id === theme.value) ?? THEMES[0]
)

/**
 * Put the theme on the document.
 *
 * Called eagerly on import as well as from `setTheme`: this module is
 * imported before the app mounts, so the attribute is already correct
 * when the first frame paints. Doing it only in `setTheme` would mean a
 * page restored from `localStorage` flashed the default theme first.
 */
function apply(id: ThemeId): void {
  document.documentElement.dataset.theme = id
}

apply(theme.value)

/**
 * Switch theme, persist it, and tell the JS-side consumers to rebuild.
 *
 * Unknown ids are refused with a thrown `TypeError` rather than silently
 * ignored: the only caller is the picker, which can only produce ids
 * from `THEMES`, so an unknown one is a programming error and should be
 * loud.
 */
export function setTheme(id: ThemeId): void {
  if (!isThemeId(id)) {
    throw new TypeError(`unknown theme ${String(id)}; expected one of ${THEME_IDS.join(', ')}`)
  }

  theme.value = id
  apply(id)
  // Bumped even when the id is unchanged: reopening the picker and
  // picking the current theme is still a "redraw" from the point of view
  // of anything that cached a palette.
  themeVersion.value += 1

  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    // Storage refused: the theme applies for this page load and is lost
    // on refresh. Worth doing rather than worth failing over.
  }
}

/** Cycle to the next theme — the shortest possible control for three options. */
export function nextTheme(): ThemeId {
  const index = THEME_IDS.indexOf(theme.value)
  const next = THEMES[(index + 1) % THEMES.length]
  setTheme(next.id)
  return next.id
}
