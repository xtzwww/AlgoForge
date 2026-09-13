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
 */

/** Base palette (raw values — do not use these directly in components). */
export const palette = {
  // Surfaces, darkest to lightest
  canvas: '#0b1020',
  surface: '#141b2d',
  elevated: '#1c2438',
  overlay: '#232c42',
  border: '#263149',
  borderStrong: '#33415c',

  // Text
  textPrimary: '#e8ecf5',
  textSecondary: '#9aa7bd',
  textMuted: '#66738c',
  textInverse: '#0b1020',

  // Brand / accent
  accent: '#4f8cff',
  accentHover: '#6ba1ff',
  accentActive: '#3d78e6',
  accentSubtle: '#1e2f52',

  // Semantic
  success: '#22c55e',
  warning: '#f59e0b',
  danger: '#ef4444',
  info: '#06b6d4',
  purple: '#8b5cf6',
  orange: '#f97316',
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
  UNKNOWN: palette.textSecondary,
} as const

export type Verdict = keyof typeof verdictColor

/** Look up a verdict color, falling back to UNKNOWN for unknown statuses. */
export function colorForVerdict(status: string): string {
  return (verdictColor as Record<string, string>)[status] ?? verdictColor.UNKNOWN
}

/** Layout constants shared between CSS and JS (e.g. Monaco sizing). */
export const layout = {
  sidebarWidth: 220,
  sidebarWidthCollapsed: 60,
  topbarHeight: 48,
  problemPanelWidth: 360,
} as const
