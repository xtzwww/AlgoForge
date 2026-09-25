/**
 * Design tokens — the single source of truth for AlgoForge's visual language.
 *
 * PU-0: before this file existed there were 89 hardcoded hex values scattered
 * across 7 .vue files, and the same verdict (e.g. MLE) had different colors in
 * different components. Every color in the app must come from here.
 *
 * Consumed by:
 *   - tokens.css  (CSS custom properties, for styles and Tailwind utilities)
 *   - monaco-theme.ts (Monaco's defineTheme needs literal hex, not var())
 *
 * IMPORTANT: Tailwind v4's `@theme` block is generated FROM this file's values
 * (see the note in tokens.css). If you change a value here, update tokens.css
 * to match — or better, treat this file as canonical and keep them in sync.
 *
 * 2026-09-25 — palette replaced wholesale with 「霓虹深空」. The previous
 * values were the generic dark-navy-plus-one-blue that any admin template
 * produces; the reasoning and the alternatives considered are in
 * `docs/redesign-preview.html`. What changed in substance:
 *
 *   - the canvas is deeper (#070a14) and the accent moved from a flat
 *     blue to a blue-violet pair, so a gradient has two real ends;
 *   - the semantic colours were brightened — a verdict badge is now
 *     legible *and* glows, which the old `#22c55e` could not do on a
 *     surface this dark;
 *   - `accentAlt` is new and is NOT a shade of `accent`: it is the other
 *     end of the accent gradient, and naming it is what stops a second
 *     literal appearing in whichever component builds that gradient.
 *
 * Motion and glow values live in `tokens.css` only — nothing in JS reads
 * them, and inventing exports nobody imports is how a "source of truth"
 * starts to look authoritative while being wrong.
 */

/** Base palette (raw values — do not use these directly in components). */
export const palette = {
  // Surfaces, darkest to lightest
  canvas: '#070a14',
  surface: '#101728',
  elevated: '#172036',
  overlay: '#1e2942',
  border: '#22304d',
  borderStrong: '#35486e',

  // Text
  textPrimary: '#eef2ff',
  textSecondary: '#a9b7d6',
  textMuted: '#6d7c9e',
  textInverse: '#070a14',

  // Brand / accent
  accent: '#6d8dff',
  accentHover: '#8aa5ff',
  accentActive: '#5878ee',
  accentSubtle: '#1b2750',
  /** The violet end of the accent gradient. See the file docstring. */
  accentAlt: '#9d7bff',

  // Semantic
  success: '#34e39a',
  warning: '#ffb347',
  danger: '#ff6b81',
  info: '#38d6f0',
  purple: '#a78bfa',
  orange: '#ff9455'
} as const

/**
 * Verdict colors — one per backend JudgeStatus.
 *
 * Keys MUST stay in sync with `sidecar/app/sandbox/judge.py::JudgeStatus`
 * (AC / WA / TLE / MLE / CE / RE). The values below preserve the mapping
 * already in use in OutputPanel.vue so nothing visibly changes during PU-0;
 * PU-8 will consume them via the <VerdictBadge> component instead of
 * redeclaring them per file.
 */
export const verdictColor = {
  AC: palette.success,
  WA: palette.danger,
  TLE: palette.warning,
  MLE: palette.info,
  CE: palette.purple,
  RE: palette.orange,
  /** Not a JudgeStatus — used when a status string is unrecognised. */
  UNKNOWN: palette.textSecondary
} as const

export type Verdict = keyof typeof verdictColor

/** Look up a verdict color, falling back to UNKNOWN for unknown statuses. */
export function colorForVerdict(status: string): string {
  return (verdictColor as Record<string, string>)[status] ?? verdictColor.UNKNOWN
}

/**
 * Mastery ramp (PU-9) — the four bands an accuracy figure falls into.
 *
 * Lives here as well as in `tokens.css` because ECharts needs literal
 * hex: `RadarChart.vue` cannot read a CSS variable into a series option,
 * so it reads `--af-mastery-*` off the computed style at mount. Keeping
 * the hex in this file too means a reader finds the ramp next to the
 * palette rather than only inside a component's mount hook.
 *
 * Ordered weakest → strongest; `masteryColor()` is the only thing that
 * should index it.
 */
export const masteryColor = {
  weak: palette.danger,
  poor: palette.orange,
  fair: '#f2d24b',
  good: palette.success
} as const

/**
 * Map an accuracy in [0, 1] to one of the four mastery bands.
 *
 * The bands are 0.8 / 0.6 / 0.4, matching `ProfileView`'s pre-PU-9
 * thresholds so nothing shifts visually. Exported rather than inlined in
 * the view because the radar, the table rows and the summary figure all
 * grade the same number and must agree on the answer.
 */
export function masteryColorFor(accuracy: number): string {
  if (accuracy >= 0.8) return masteryColor.good
  if (accuracy >= 0.6) return masteryColor.fair
  if (accuracy >= 0.4) return masteryColor.poor
  return masteryColor.weak
}

/** Difficulty badge colours — keys match `ProblemSummary.difficulty`. */
export const difficultyColor = {
  easy: palette.success,
  medium: palette.warning,
  hard: palette.danger
} as const

/** Layout constants shared between CSS and JS (e.g. Monaco sizing). */
export const layout = {
  sidebarWidth: 220,
  sidebarWidthCollapsed: 60,
  topbarHeight: 48,
  problemPanelWidth: 360,
  /** Max width of a page's content column; see tokens.css (PU-9). */
  contentMaxWidth: 1080
} as const
