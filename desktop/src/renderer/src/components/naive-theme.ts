/**
 * Naive UI ← AlgoForge 设计令牌 的桥接（2026-09-26）。
 *
 * 为什么需要这一层
 * ----------------
 * Naive UI 的颜色是**配置出来的**，不是 CSS 变量驱动的——它有自己的
 * `themeOverrides`，一份 JS 对象，逐项指名颜色、圆角、字号。
 * 而本项目的颜色在 CSS 里以 `--af-*` 存着，并且有**三套主题**
 * （`neon` 默认 / `terminal` / `paper`，见 `assets/themes.css`）。
 *
 * 两者必须对接，否则会出现本文件存在要避免的那个后果：**Naive 的组件
 * 用自己那套配色，页面其余部分用 `--af-*`，同一个屏幕上有两套颜色**，
 * 而这正是这次升级要消掉的问题（见 `docs/` 里的组件库升级方案）。
 *
 * 怎么对接
 * --------
 * 不去读 CSS 变量（那需要 `getComputedStyle`，在测试环境里会拿到空串，
 * 而且首次渲染时还没应用主题）。改成**在 JS 里也维护一份值**：
 * `assets/tokens.ts` 已经是 `--af-*` 的镜像（tokens.css 的文件头写着
 * "change one, change both"），这里再挂上 `themes.css` 里那三套的差异
 * 部分。
 *
 * 于是三处一致：`tokens.css`（CSS 用）、`tokens.ts`（图表/JS 用）、
 * 本文件（组件库用）。**改配色要同时改这几处**——这是这个方案唯一的
 * 真实代价，写在文件头就是为了让它显眼。
 *
 * 颜色用 `rgb()` 而不是 `var(--af-x)`
 * ----------------------------------
 * Naive UI 内部会对颜色做**运算**（算 hover 态、算派生色、算透明度）。
 * 喂给它 `var(--af-surface)` 会让那些运算拿到字符串而不是颜色，结果是
 * 组件静默地变成透明或黑色。所以这里必须是具体色值。
 *
 * 色值从 `themes.css` 抄来，**不是自己定的**。
 */

import { computed, type ComputedRef } from 'vue'
import type { GlobalTheme, GlobalThemeOverrides } from 'naive-ui'
import { darkTheme } from 'naive-ui'
import { theme } from '../composables/useTheme'

/** 一个主题下 Naive UI 需要的全部颜色。字段名对应 `--af-*`，好核对。 */
interface ThemePalette {
  canvas: string
  surface: string
  elevated: string
  overlay: string
  border: string
  borderStrong: string
  textPrimary: string
  textSecondary: string
  textMuted: string
  textInverse: string
  accent: string
  accentHover: string
  accentActive: string
  accentSubtle: string
  success: string
  warning: string
  danger: string
  info: string
  /** 是否深色底。决定用 `darkTheme` 还是 null，以及投影强度。 */
  dark: boolean
}

/**
 * `neon` —— 默认主题。值抄自 `tokens.css` 的 `:root`。
 *
 * 它是**半透明毛玻璃**底：`--af-surface` 不透明，但面板实际叠在带辉光的
 * canvas 上。给 Naive 的弹窗用 `surface` 而不是 `canvas`，因为弹窗必须
 * 读得清，而它不是毛玻璃（Naive 的遮罩会把背后的辉光压掉）。
 */
const NEON: ThemePalette = {
  canvas: '#070a14',
  surface: '#101728',
  elevated: '#172036',
  overlay: '#1e2942',
  border: 'rgb(122 162 255 / 0.14)',
  borderStrong: 'rgb(122 162 255 / 0.3)',
  textPrimary: '#eef2ff',
  textSecondary: '#a9b7d6',
  textMuted: '#6d7c9e',
  textInverse: '#070a14',
  accent: '#6d8dff',
  accentHover: '#8aa5ff',
  accentActive: '#5878ee',
  accentSubtle: '#1b2750',
  success: '#34e39a',
  warning: '#ffb347',
  danger: '#ff6b81',
  info: '#38d6f0',
  dark: true
}

/** `terminal` —— 赛制终端。抄自 `themes.css` 的 `[data-theme='terminal']`。 */
const TERMINAL: ThemePalette = {
  canvas: '#08090b',
  surface: '#0f1115',
  elevated: '#15181e',
  overlay: '#1b1f26',
  // 这个主题的边框是**不透明**的：没有毛玻璃，边框后面没有东西可透，
  // 14% 透明度的细线在近黑底上等于看不见（themes.css 里写明了）。
  border: '#21252c',
  borderStrong: '#333941',
  textPrimary: '#e7eaee',
  textSecondary: '#9aa1aa',
  textMuted: '#666c75',
  textInverse: '#08090b',
  accent: '#d8a227',
  accentHover: '#e8b84a',
  accentActive: '#bf8f22',
  accentSubtle: '#2a2415',
  success: '#2ee6a8',
  warning: '#d8a227',
  danger: '#ff5c5c',
  info: '#4fd6ff',
  dark: true
}

/** `paper` —— 唯一一套浅色主题。抄自 `[data-theme='paper']`。 */
const PAPER: ThemePalette = {
  canvas: '#f7f4ee',
  surface: '#fffefb',
  elevated: '#f1ece2',
  overlay: '#e8e1d3',
  border: '#e4ddd0',
  borderStrong: '#d3c9b7',
  textPrimary: '#1c1a16',
  textSecondary: '#5d574d',
  textMuted: '#918a7c',
  // 浅色主题下强调色是**深**蓝，所以压在它上面的字是浅色——和深色主题
  // 正好相反（themes.css 里专门警告过这一点）。
  textInverse: '#fffefb',
  accent: '#1f4e79',
  accentHover: '#2d6ba5',
  accentActive: '#16405f',
  accentSubtle: '#e3ecf5',
  success: '#2f7a4d',
  warning: '#a8700f',
  danger: '#b03a2e',
  info: '#2a6f8a',
  dark: false
}

const PALETTES: Record<string, ThemePalette> = {
  neon: NEON,
  terminal: TERMINAL,
  paper: PAPER
}

/**
 * 把一套调色板翻成 Naive 的 `themeOverrides`。
 *
 * 只覆盖到"能看出来"的层面。没写的项走 Naive 的默认值——默认值和这里的
 * 值是同一族的，因为底座（`darkTheme` / null）已经决定了明暗关系；逐项
 * 强写一遍既冗长又会在 Naive 升级时变成负担。
 */
function overridesFor(p: ThemePalette): GlobalThemeOverrides {
  return {
    common: {
      primaryColor: p.accent,
      primaryColorHover: p.accentHover,
      primaryColorPressed: p.accentActive,
      primaryColorSuppl: p.accentHover,

      successColor: p.success,
      warningColor: p.warning,
      errorColor: p.danger,
      infoColor: p.info,

      // 组件底：弹窗、下拉、卡片都取它。
      bodyColor: p.canvas,
      cardColor: p.surface,
      modalColor: p.surface,
      popoverColor: p.surface,
      tableColor: p.surface,
      inputColor: p.canvas,

      // 分层与交互态。
      hoverColor: p.elevated,
      pressedColor: p.overlay,
      borderColor: p.border,
      dividerColor: p.border,

      textColorBase: p.textPrimary,
      textColor1: p.textPrimary,
      textColor2: p.textPrimary,
      textColor3: p.textSecondary,
      textColorDisabled: p.textMuted,
      placeholderColor: p.textMuted,
      // 反色字：Naive 在强调色填充上用它。
      baseColor: p.dark ? '#000' : '#fff',

      borderRadius: '8px',
      borderRadiusSmall: '6px',
      fontSize: '14px',
      fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      // 主题自带的投影：深色主题靠描边而不是投影分层（themes.css 的
      // terminal 一节写明了这个取舍），所以这里比默认轻。
      boxShadow1: p.dark ? `0 1px 2px rgba(0, 0, 0, 0.5)` : `0 1px 2px rgba(28, 26, 22, 0.08)`,
      boxShadow2: p.dark ? `0 6px 18px rgba(0, 0, 0, 0.55)` : `0 6px 18px rgba(28, 26, 22, 0.12)`,
      boxShadow3: p.dark ? `0 12px 32px rgba(0, 0, 0, 0.6)` : `0 12px 32px rgba(28, 26, 22, 0.16)`
    },
    Dialog: {
      // 遮罩要能把背后的辉光压住，否则弹窗读不清——这是毛玻璃风格里
      // 唯一的例外：弹窗不是玻璃。
      color: p.surface,
      textColor: p.textPrimary,
      titleTextColor: p.textPrimary
    },
    Button: {
      textColorPrimary: p.textInverse,
      textColorHoverPrimary: p.textInverse,
      textColorPressedPrimary: p.textInverse,
      textColorFocusPrimary: p.textInverse,
      // 禁用态用平底而不是降透明度：变暗的强调色仍读作"主操作"，
      // 会招来一次什么都不会发生的点击（和旧 `af-t-primary` 同一条规则）。
      colorPrimary: p.accent,
      colorHoverPrimary: p.accentHover,
      colorPressedPrimary: p.accentActive,
      colorDisabled: p.elevated,
      textColorDisabled: p.textMuted,
      borderDisabled: p.border
    },
    Input: {
      color: p.canvas,
      colorFocus: p.canvas,
      border: `1px solid ${p.borderStrong}`,
      borderHover: `1px solid ${p.accent}`,
      borderFocus: `1px solid ${p.accent}`,
      boxShadowFocus: `0 0 0 2px ${p.accentSubtle}`
    },
    Select: {
      peers: {
        InternalSelection: {
          color: p.canvas,
          border: `1px solid ${p.borderStrong}`,
          borderHover: `1px solid ${p.accent}`,
          borderFocus: `1px solid ${p.accent}`,
          borderActive: `1px solid ${p.accent}`,
          boxShadowFocus: `0 0 0 2px ${p.accentSubtle}`,
          textColor: p.textPrimary,
          placeholderColor: p.textMuted
        },
        InternalSelectMenu: {
          color: p.surface,
          optionTextColor: p.textPrimary,
          optionTextColorActive: p.accent,
          optionColorPending: p.elevated,
          optionCheckColor: p.accent
        }
      }
    },
    Card: {
      color: p.surface,
      borderColor: p.border,
      titleTextColor: p.textPrimary,
      textColor: p.textSecondary
    },
    DataTable: {
      thColor: p.elevated,
      thTextColor: p.textMuted,
      tdColor: p.surface,
      tdColorHover: p.elevated,
      borderColor: p.border,
      thFontWeight: '500'
    },
    Checkbox: {
      color: p.canvas,
      colorChecked: p.accent,
      border: `1px solid ${p.borderStrong}`,
      borderChecked: `1px solid ${p.accent}`
    },
    Message: {
      color: p.surface,
      textColor: p.textPrimary,
      boxShadow: p.dark ? `0 6px 18px rgba(0, 0, 0, 0.55)` : `0 6px 18px rgba(28, 26, 22, 0.12)`
    }
  }
}

/**
 * 给 `NConfigProvider` 用的主题三件套。
 *
 * `theme` 只在深色主题下给 `darkTheme`：Naive 的 `darkTheme` 除了颜色，
 * 还带着一整套深色下的**明暗关系**（比如哪些表面比哪些更亮）。浅色的
 * `paper` 给 `null`，也就是 Naive 的亮色底座。
 *
 * 返回的是 `computed`，因为主题可以中途切换（`useTheme`），而 Naive 的
 * 主题是**响应式 prop**——传一个死对象进去，切换主题时组件不会重算。
 */
/**
 * 这两个是只读 `computed`，不是 `WritableComputedRef`：主题由 `useTheme`
 * 单方面决定，这里的返回值只供 `NConfigProvider` 读取。写成可写会让调用方
 * 以为 `themeOverrides.value = …` 能改变主题，而它不能。
 */
export function useNaiveTheme(): {
  theme: ComputedRef<GlobalTheme | null>
  themeOverrides: ComputedRef<GlobalThemeOverrides>
} {
  const palette = computed<ThemePalette>(() => PALETTES[theme.value] ?? NEON)

  return {
    theme: computed(() => (palette.value.dark ? darkTheme : null)),
    themeOverrides: computed(() => overridesFor(palette.value))
  }
}
