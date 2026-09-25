/**
 * Monaco theme + language registration (PU-10).
 *
 * Two jobs, both of them about Monaco's inability to see the app's
 * design system.
 *
 * ---------------------------------------------------------------------
 * 1. The theme
 * ---------------------------------------------------------------------
 *
 * Monaco's `defineTheme` takes literal colours — `keyword: 'var(--af-purple)'`
 * is accepted without complaint and paints the token *black*, because the
 * value is handed to a canvas painter rather than to the CSS engine. This
 * is the same constraint that forced `RadarChart.vue` to resolve its
 * tokens at runtime (PU-9), and it gets the same answer: read the token
 * once through `getComputedStyle`, with a literal fallback per token.
 *
 * The fallbacks are not decoration. `getPropertyValue()` returns the
 * **empty string** for a name it does not know — it does not throw, and
 * it does not tell you it failed. A missing fallback therefore produces
 * transparent text on a themed background, which reads as "the editor is
 * broken" rather than "the token moved". The literals below come from
 * `./assets/tokens`, which is where the same values are declared for JS
 * consumers, so the fallback and the token are at least named by one
 * symbol.
 *
 * Why not read the tokens *only* from `tokens.ts` and skip the computed
 * style? Because that would make the editor immune to a future
 * `prefers-color-scheme` block in `tokens.css`: the CSS would change and
 * the editor would silently keep the old colours. The runtime read keeps
 * the stylesheet authoritative; `tokens.ts` supplies the fallbacks.
 *
 * ---------------------------------------------------------------------
 * 2. Language registration — and an assumption this slice disproved
 * ---------------------------------------------------------------------
 *
 * The plumbing below exists because Monaco's language registry is global
 * and the app must not assume an id is usable. What it does **not** do
 * any more is substitute one language's grammar for another's.
 *
 * The original plan for this slice assumed Monaco's ESM build ships no
 * Java tokenizer, and mapped `java` onto `cpp` so the buffer would at
 * least be coloured. That assumption was wrong, and the check that
 * disproved it is cheap enough to be worth recording:
 *
 *     node_modules/monaco-editor/esm/vs/index.js
 *       line 29: import './languages/definitions/java/register.js'
 *
 * `java` is registered by the package's own entry point, so
 * `monaco.editor.create({ language: 'java' })` highlights Java
 * correctly, and the `java-*.js` tokenizer chunk was already present in
 * both build outputs before this slice. (The earlier `ls
 * esm/vs/basic-languages/` that suggested otherwise was looking at a
 * directory that contains only a re-export shim in this version.)
 *
 * The lesson is worth more than the code: **a substitution table is a
 * claim about a third-party package, and that claim is verifiable in one
 * grep.** Shipping the fallback would have silently degraded Java
 * highlighting — a real user-visible regression, introduced to fix a
 * problem that did not exist, and invisible to every one of this
 * project's build-level checks. What remains is the honest version:
 * report whether an id is registered, and say so if it is not.
 */

import type * as monacoNs from 'monaco-editor'
import monaco from './monaco'
import { palette } from './assets/tokens'
import { theme } from './composables/useTheme'

/** Monaco's theme name for AlgoForge. Referenced by `CodeEditor.vue`. */
export const ALGOFORGE_THEME = 'algoforge-dark'

/**
 * Monaco language ids this app may name.
 *
 * Deliberately a union rather than `string`. `CodeEditor.vue` passes its
 * value straight into `monaco.editor.create`, and an id Monaco has never
 * heard of does **not** throw — it produces a buffer with no
 * highlighting, which reads as a broken editor. Narrowing the type moves
 * that from "discovered by looking at it" to "rejected by the compiler".
 *
 * `plaintext` is Monaco's own id for "no grammar"; it is the correct
 * value for a buffer whose language is genuinely unknown, and is
 * preferable to guessing.
 */
export type MonacoLanguageId = 'cpp' | 'python' | 'java' | 'plaintext'

let installed = false

/**
 * Read one CSS custom property, falling back to the literal when the
 * variable is absent.
 *
 * Trimming matters: `getPropertyValue` preserves the whitespace the
 * author wrote in the declaration, and Monaco would then receive
 * `" #4f8cff"`, which it parses as an invalid colour.
 */
function readToken(styles: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = styles.getPropertyValue(name).trim()
  return value || fallback
}

/**
 * Convert a token into the `#RRGGBB` / `#RRGGBBAA` form `defineTheme`
 * demands.
 *
 * Monaco validates its `colors` map against that shape and — crucially —
 * *warns and ignores* anything else rather than throwing. `--af-border-strong`
 * is written `rgb(122 162 255 / 0.3)`: a hairline border has to be
 * translucent to pick up the canvas behind it, which an opaque hex cannot
 * express (see tokens.css). Passed through raw, Monaco would drop it and
 * leave the widget border on the `vs-dark` default, with nothing visible
 * in the UI to say why.
 *
 * Alpha survives as the 8-digit form, which Monaco does parse — losing it
 * would turn a hairline into a solid line and undo the reason the token is
 * translucent.
 */
function toMonacoHex(color: string): string {
  const value = color.trim()

  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(value)
  if (hex) {
    // #rgb → #rrggbb. The 8-digit form is already what we want.
    return hex[1].length === 3 ? `#${hex[1].replace(/./g, (c) => c + c)}` : value
  }

  // `rgb(r, g, b)`, `rgba(r, g, b, a)` and the CSS Color 4
  // `rgb(r g b / a)` all reduce to three channels plus an optional alpha
  // once the separators stop mattering — which is what this does.
  const channels =
    /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[,/]\s*([\d.]+%?)\s*)?\)$/i.exec(value)
  if (!channels) return value

  const byte = (n: number): string =>
    Math.max(0, Math.min(255, Math.round(n)))
      .toString(16)
      .padStart(2, '0')

  const [, r, g, b, rawAlpha] = channels
  const base = `#${byte(Number.parseFloat(r))}${byte(Number.parseFloat(g))}${byte(Number.parseFloat(b))}`
  if (rawAlpha === undefined) return base

  const alpha = rawAlpha.endsWith('%')
    ? Number.parseFloat(rawAlpha) / 100
    : Number.parseFloat(rawAlpha)
  return `${base}${byte(alpha * 255)}`
}

/**
 * A token resolved to a literal colour, ready for `defineTheme`.
 *
 * The named fields are exactly the slots the theme reads; everything
 * else is inherited from `vs-dark`, which is the base.
 */
interface ThemePalette {
  background: string
  foreground: string
  /** Line numbers, gutter text — dimmer than the code. */
  gutter: string
  /** The active line number / the line the cursor is on. */
  gutterActive: string
  /** The `current-line` highlight; a surface step, not a colour. */
  lineHighlight: string
  /** The editor's own selection background. */
  selection: string
  /** Text selected while the editor does not have focus. */
  selectionInactive: string
  /** `editorWidget` (find box, suggest popup) background. */
  widget: string
  /** The border Monaco draws on widgets and on the scrollbar rail. */
  widgetBorder: string
  /** Bracket-pair matcher, cursor and the active scrollbar slider. */
  accent: string
  /** Syntax slots. */
  comment: string
  keyword: string
  string: string
  number: string
  type: string
  function: string
  variable: string
  operator: string
  tag: string
  constant: string
  invalid: string
}

/**
 * Resolve the PU-0 tokens into the literals `defineTheme` demands.
 *
 * Read fresh on each call rather than cached: this runs exactly once per
 * app start (see {@link installAlgoforgeTheme}), so a cache would only be
 * a second source of truth for something that cannot change twice.
 */
function resolveThemePalette(): ThemePalette {
  const styles = getComputedStyle(document.documentElement)

  /**
   * Every read goes through `toMonacoHex()`. These strings are handed to
   * Monaco, not to CSS, and Monaco's tolerance for CSS colour syntax is
   * much narrower than the browser's — see `toMonacoHex`.
   */
  const token = (name: string, fallback: string): string =>
    toMonacoHex(readToken(styles, name, fallback))

  const accent = token('--af-accent', palette.accent)

  return {
    background: token('--af-surface', palette.surface),
    foreground: token('--af-text-primary', palette.textPrimary),

    // The gutter is deliberately one step *below* the app's muted text:
    // line numbers sit behind the code and should recede further than a
    // label does, or a long file becomes a wall of digits.
    gutter: token('--af-text-muted', palette.textMuted),
    gutterActive: token('--af-text-secondary', palette.textSecondary),

    lineHighlight: token('--af-elevated', palette.elevated),
    selection: token('--af-accent-subtle', palette.accentSubtle),
    selectionInactive: token('--af-overlay', palette.overlay),

    widget: token('--af-elevated', palette.elevated),
    widgetBorder: token('--af-border-strong', palette.borderStrong),

    accent,

    // Syntax. These are not their own names in `tokens.css` because they
    // are not a *design* decision — they are a readability decision that
    // happens to reuse the semantic palette. Giving them tokens would
    // invite them being used for UI, where `purple` means "compile
    // error" and not "keyword".
    comment: token('--af-text-muted', palette.textMuted),
    keyword: token('--af-purple', palette.purple),
    string: token('--af-success', palette.success),
    number: token('--af-orange', palette.orange),
    type: token('--af-info', palette.info),
    function: accent,
    variable: token('--af-text-primary', palette.textPrimary),
    operator: token('--af-text-secondary', palette.textSecondary),
    tag: token('--af-danger', palette.danger),
    constant: token('--af-warning', palette.warning),
    invalid: token('--af-danger', palette.danger)
  }
}

/**
 * Build the `defineTheme` rule list from a resolved palette.
 *
 * `token` values are the DotNet/TextMate-ish names Monaco's tokenizers
 * emit. Unlisted names inherit from `vs-dark`, so a token nobody styled
 * keeps a sane colour instead of going transparent.
 */
function buildRules(p: ThemePalette): monacoNs.editor.ITokenThemeRule[] {
  return [
    { token: 'comment', foreground: p.comment, fontStyle: 'italic' },
    { token: 'keyword', foreground: p.keyword },
    { token: 'keyword.control', foreground: p.keyword },
    { token: 'string', foreground: p.string },
    { token: 'string.escape', foreground: p.constant },
    { token: 'number', foreground: p.number },
    { token: 'number.hex', foreground: p.number },
    { token: 'regexp', foreground: p.number },
    { token: 'type', foreground: p.type },
    { token: 'type.identifier', foreground: p.type },
    { token: 'namespace', foreground: p.type },
    { token: 'function', foreground: p.function },
    { token: 'identifier', foreground: p.variable },
    // What the C++ and Java tokenizers emit for `NULL`, `true` and the
    // like; the constant colour is what separates them from an ordinary
    // variable.
    { token: 'variable.predefined', foreground: p.constant },
    { token: 'operator', foreground: p.operator },
    { token: 'delimiter', foreground: p.operator },
    { token: 'tag', foreground: p.tag },
    { token: 'attribute.name', foreground: p.tag },
    { token: 'invalid', foreground: p.invalid }
  ]
}

/**
 * The Monaco base the active theme needs.
 *
 * Neon and Terminal are dark surfaces, so they inherit from `vs-dark`;
 * Paper is a light surface (projector / printed handout), and a dark
 * base under light `--af-*` colours would render light-on-light text.
 * Switching the base with the theme is what keeps the editor legible in
 * all three — see the `themeVersion` watcher in `CodeEditor.vue`, which
 * re-applies this theme after every switch.
 */
function baseForTheme(): 'vs' | 'vs-dark' {
  return theme.value === 'paper' ? 'vs' : 'vs-dark'
}

/**
 * Resolve the current tokens and (re)define the AlgoForge theme.
 *
 * `defineTheme` with an already-registered name updates every live
 * editor that uses it, so this is safe to call repeatedly. The base is
 * re-read each call, which is why a Paper switch takes effect rather
 * than being frozen at the first install.
 */
function defineNow(): void {
  const p = resolveThemePalette()

  monaco.editor.defineTheme(ALGOFORGE_THEME, {
    // `vs-dark` is the base for the dark themes so the ~200 token names
    // we do not list keep a readable inherited colour. `base: 'vs'` for
    // Paper needs no slot to be filled: the light base inherits light
    // defaults, and our explicit `colors` override the surface.
    base: baseForTheme(),
    inherit: true,
    rules: buildRules(p),
    colors: {
      'editor.background': p.background,
      'editor.foreground': p.foreground,
      'editorLineNumber.foreground': p.gutter,
      'editorLineNumber.activeForeground': p.gutterActive,
      'editor.lineHighlightBackground': p.lineHighlight,
      'editor.selectionBackground': p.selection,
      'editor.inactiveSelectionBackground': p.selectionInactive,
      'editorCursor.foreground': p.accent,
      'editorWidget.background': p.widget,
      'editorWidget.border': p.widgetBorder,
      'editorSuggestWidget.background': p.widget,
      'editorSuggestWidget.border': p.widgetBorder,
      'editorSuggestWidget.selectedBackground': p.selection,
      'editorHoverWidget.background': p.widget,
      'editorHoverWidget.border': p.widgetBorder,
      'editorBracketMatch.background': p.selection,
      'editorBracketMatch.border': p.accent,
      'editorIndentGuide.background1': p.widgetBorder,
      'editorIndentGuide.activeBackground1': p.gutter,
      // The overview ruler sits against the editor background rather than
      // the canvas; without this it keeps vs-dark's grey and reads as a
      // foreign element on a themed editor.
      'editorOverviewRuler.border': p.background,
      'scrollbarSlider.background': p.widgetBorder,
      'scrollbarSlider.hoverBackground': p.gutter,
      'scrollbarSlider.activeBackground': p.accent
    }
  })
}

/**
 * Register the AlgoForge theme with Monaco.
 *
 * Idempotent, and must run before the first editor is created —
 * `monaco.editor.create({ theme })` with an unregistered name silently
 * falls back to `vs` (light), so a missed call looks like "the theme
 * didn't apply" rather than an error.
 *
 * Called from `CodeEditor.vue`'s module scope rather than from
 * `main.ts`, so the theme and its only consumer cannot get separated:
 * importing the component is what installs the theme.
 */
export function installAlgoforgeTheme(): void {
  if (installed) return
  installed = true
  defineNow()
}

/**
 * Re-resolve tokens and redefine the theme after a visual-style switch.
 *
 * The theme's literals are read from CSS, so when `data-theme` changes
 * the resolved colours differ. `CodeEditor.vue` watches `themeVersion`
 * and calls this, then re-applies the theme name — redefining with a
 * new `base` (Paper is `vs`, the dark themes are `vs-dark`) does not
 * take effect on a live editor until `setTheme` is called again.
 */
export function refreshAlgoforgeTheme(): void {
  defineNow()
}

/**
 * Whether Monaco has a tokenizer registered for *id*.
 *
 * `languages.getLanguages()` is the registry's own answer, so this stays
 * true even if a future `monaco-editor` bump adds or drops a grammar —
 * unlike a hand-maintained list, which is exactly the kind of claim this
 * slice already got wrong once (see the header).
 *
 * Note this is asked *after* registration has happened, which is why
 * `CodeEditor.vue` calls it in a computed rather than at module scope.
 */
export function hasNativeHighlighting(id: string): boolean {
  return monaco.languages.getLanguages().some((l) => l.id === id)
}
