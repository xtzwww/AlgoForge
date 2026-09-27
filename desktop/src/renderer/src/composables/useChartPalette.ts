import { onMounted, ref, watch, type Ref } from 'vue'
import { themeVersion } from './useTheme'

/**
 * useChartPalette — 所有 ECharts 组件共用的调色板解析（PU-9 的雷达逻辑抽出来）。
 *
 * ECharts 的 option 只收字面色值，不收 `var(--af-*)`。而本项目有三套可运行时
 * 切换的主题（neon / terminal / paper，见 assets/themes.css），所以图表必须
 * 在**运行时**从 CSS 变量解析颜色，并且监听 `themeVersion` 在切换主题时重画。
 *
 * 为什么抽成 composable 而不是让每个图表复制一份：
 *   - 颜色解析的失败模式是静默的（见 `normalizeColor` / `readToken` 的注释），
 *     复制四份就是四份各自漂移的实现——PU-0 就是为了消灭这种复制。
 *   - 新图表（BarChart / PieChart / 未来的 LineChart）拿到的是一份已测过的逻辑，
 *     而不是一份"看起来差不多的拷贝"。
 *
 * 用法：
 *   const { palette } = useChartPalette()
 *   // palette 在 onMounted 前为 null；template 用 v-else 分支兜住，
 *   // option computed 在 palette 为 null 时返回 {}。
 */

export interface ChartPalette {
  textPrimary: string
  textSecondary: string
  textMuted: string
  grid: string
  band: string
  bandAlt: string
  surface: string
  accent: string
  success: string
  danger: string
  warning: string
}

/**
 * Token name → fallback literal。
 *
 * Keyed by the resolved *value*, so the option builder does not care
 * whether the tokens were found. See RadarChart's docstring: empty-string
 * returns are the failure mode this table exists to absorb.
 */

/**
 * Read one token, falling back when the variable is missing.
 *
 * Trimmed because `getPropertyValue` preserves the author's leading
 * whitespace — `--af-surface: #141b2d;` yields `" #141b2d"`, which ECharts
 * tolerates but which makes an equality check against a literal fail.
 */
export function readToken(styles: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = styles.getPropertyValue(name).trim()
  return value || fallback
}

/**
 * Normalise a token into a form ECharts can actually parse.
 *
 * zrender's colour parser understands hex and the *comma* forms
 * `rgb(r, g, b)` / `rgba(r, g, b, a)`. It does **not** understand the
 * CSS Color 4 space-and-slash form `rgb(r g b / a)` — which is exactly
 * the form `--af-border` takes, because a hairline border is genuinely
 * translucent and an opaque hex cannot express that (see tokens.css).
 *
 * This matters because several things pass a token to ECharts *without*
 * going through `withAlpha()` below — tooltip borders and grid/axis lines —
 * and all read `--af-border`. Un-normalised, they hand zrender a string it
 * cannot parse, and it does not throw: it silently paints something else.
 * A grid that quietly disappears is much harder to notice than one that
 * errors.
 *
 * Unlike `withAlpha()`, alpha is *preserved* here; that function exists
 * to override it.
 */
export function normalizeColor(color: string): string {
  const value = color.trim()

  // Already zrender-parseable: hex, and both comma forms.
  if (/^#[0-9a-f]{3,8}$/i.test(value)) return value
  if (/^rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(,\s*[\d.]+\s*)?\)$/i.test(value)) return value

  // `rgb(r g b)` and `rgb(r g b / a)` — the CSS Color 4 syntax.
  const modern = /^rgba?\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+%?)\s*)?\)$/i.exec(value)
  if (modern) {
    const [, r, g, b, rawAlpha] = modern
    if (rawAlpha === undefined) return `rgb(${r}, ${g}, ${b})`
    // A percentage alpha is also valid CSS Color 4 and also unparseable
    // by zrender, so it is converted rather than passed along.
    const alpha = rawAlpha.endsWith('%')
      ? String(Number.parseFloat(rawAlpha) / 100)
      : rawAlpha
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }

  // Unrecognised: hand it over unchanged rather than guessing.
  return value
}

/**
 * Converts a token — which may be hex or `rgb()` — into `rgba(..., a)`.
 */
export function withAlpha(color: string, alpha: number): string {
  const hex = color.trim()

  // #rgb / #rrggbb
  const shortHex = /^#([0-9a-f]{3})$/i.exec(hex)
  const longHex = /^#([0-9a-f]{6})$/i.exec(hex)
  if (shortHex || longHex) {
    const digits = shortHex ? shortHex[1].replace(/./g, (c) => c + c) : longHex![1]
    const n = Number.parseInt(digits, 16)
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
  }

  // rgb(r, g, b) — the form a future token might legitimately take.
  const rgb = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(hex)
  if (rgb) {
    return `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${alpha})`
  }

  // An unrecognised format returns the colour unchanged rather than
  // `rgba(NaN, ...)`: at worst the fill is opaque, which is visible.
  return hex
}

export function resolvePalette(): ChartPalette {
  const root = document.documentElement
  const styles = getComputedStyle(root)

  /*
   * The fallbacks reproduce the current palette (updated with the
   * 霓虹深空 repaint). They are dead weight whenever `tokens.css` is
   * loaded — which is always, at runtime — and exist so this function is
   * total: a chart painted in the wrong palette is a cosmetic bug, an
   * invisible one is a blank page.
   *
   * Every value goes through `normalizeColor()` because these strings are
   * handed to ECharts, not to CSS — see that function for why the
   * distinction is load-bearing.
   */
  return {
    textPrimary: normalizeColor(readToken(styles, '--af-text-primary', '#eef2ff')),
    textSecondary: normalizeColor(readToken(styles, '--af-text-secondary', '#a9b7d6')),
    textMuted: normalizeColor(readToken(styles, '--af-text-muted', '#6d7c9e')),
    grid: normalizeColor(readToken(styles, '--af-border', '#22304d')),
    band: normalizeColor(readToken(styles, '--af-elevated', '#172036')),
    bandAlt: normalizeColor(readToken(styles, '--af-surface', '#101728')),
    surface: normalizeColor(readToken(styles, '--af-surface', '#101728')),
    accent: normalizeColor(readToken(styles, '--af-accent', '#6d8dff')),
    success: normalizeColor(readToken(styles, '--af-success', '#4ade80')),
    danger: normalizeColor(readToken(styles, '--af-danger', '#f87171')),
    warning: normalizeColor(readToken(styles, '--af-warning', '#fbbf24'))
  }
}

/**
 * 挂载时解析一次，主题切换时重解析。
 *
 * 返回的 `palette` 在 mounted 之前是 `null`——option computed 在它为 null
 * 时返回 `{}`，template 用 v-else 分支渲染加载/空态，空对象不会被交给活图。
 */
export function useChartPalette(): { palette: Ref<ChartPalette | null> } {
  const palette = ref<ChartPalette | null>(null)

  onMounted(() => {
    palette.value = resolvePalette()
  })

  watch(themeVersion, () => {
    palette.value = resolvePalette()
  })

  return { palette }
}
