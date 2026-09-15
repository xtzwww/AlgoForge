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

  const accent = readToken(styles, '--af-accent', palette.accent)

  return {
    background: readToken(styles, '--af-surface', palette.surface),
    foreground: readToken(styles, '--af-text-primary', palette.textPrimary),

    // The gutter is deliberately one step *below* the app's muted text:
    // line numbers sit behind the code and should recede further than a
    // label does, or a long file becomes a wall of digits.
    gutter: readToken(styles, '--af-text-muted', palette.textMuted),
    gutterActive: readToken(styles, '--af-text-secondary', palette.textSecondary),

    lineHighlight: readToken(styles, '--af-elevated', palette.elevated),
    selection: readToken(styles, '--af-accent-subtle', palette.accentSubtle),
    selectionInactive: readToken(styles, '--af-overlay', palette.overlay),

    widget: readToken(styles, '--af-elevated', palette.elevated),
    widgetBorder: readToken(styles, '--af-border-strong', palette.borderStrong),

    accent,

    // Syntax. These are not their own names in `tokens.css` because they
    // are not a *design* decision — they are a readability decision that
    // happens to reuse the semantic palette. Giving them tokens would
    // invite them being used for UI, where `purple` means "compile
    // error" and not "keyword".
    comment: readToken(styles, '--af-text-muted', palette.textMuted),
    keyword: readToken(styles, '--af-purple', palette.purple),
    string: readToken(styles, '--af-success', palette.success),
    number: readToken(styles, '--af-orange', palette.orange),
    type: readToken(styles, '--af-info', palette.info),
    function: accent,
    variable: readToken(styles, '--af-text-primary', palette.textPrimary),
    operator: readToken(styles, '--af-text-secondary', palette.textSecondary),
    tag: readToken(styles, '--af-danger', palette.danger),
    constant: readToken(styles, '--af-warning', palette.warning),
    invalid: readToken(styles, '--af-danger', palette.danger)
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

  const p = resolveThemePalette()

  monaco.editor.defineTheme(ALGOFORGE_THEME, {
    // `vs-dark` is the base so the ~200 token names we do not list keep
    // a readable inherited colour. `base: 'vs'` would need every slot
    // filled in or the editor would be light-on-light.
    base: 'vs-dark',
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
