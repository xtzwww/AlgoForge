<script setup lang="ts">
/**
 * CodeEditor — Monaco Editor wrapper (PU-10).
 *
 * Props:
 *   modelValue (string)        — current code content (v-model)
 *   language (MonacoLanguageId) — Monaco's language id, e.g. "cpp" /
 *                                 "python" / "java". Note this is
 *                                 Monaco's id, not the sandbox's: the
 *                                 sidecar reports both, and
 *                                 `LanguageOption.monaco_language` is
 *                                 the one this component wants.
 *   theme (string)             — editor theme; defaults to the AlgoForge
 *                                theme registered by `monaco-theme.ts`.
 *
 * Emits:
 *   update:modelValue          — fired when the code changes
 *
 * **PU-10 changed two things.**
 *
 * 1. *The theme default.* `theme` used to default to `'vs-dark'`, and
 *    `Practice.vue` passed `theme="vs-dark"` explicitly on top of that —
 *    two places agreeing on a value that was not the app's. It now
 *    defaults to {@link ALGOFORGE_THEME}, and callers that have no
 *    opinion simply omit it. Keeping the prop at all is for the
 *    component's own tests and for a future light theme.
 *
 * 2. *`language` is a union, not `string`.* Passing an id Monaco has no
 *    tokenizer for does not throw — it produces a buffer with no
 *    highlighting, which reads as a broken editor. `MonacoLanguageId`
 *    makes that a compile error. The value is passed through to Monaco
 *    unchanged: `monaco-theme.ts`'s header records why an earlier plan
 *    to *substitute* one grammar for another was wrong.
 *
 * Note there is deliberately **no** `watch` on `language`. Monaco reads
 * the language only at `create()` time, so a model created as `cpp` and
 * later told to be `python` would keep C++ highlighting. The practice
 * page does not switch languages on a live buffer — a switch replaces
 * the buffer with a starter template (with a confirm dialog when the
 * buffer holds edits, see `Practice.vue::onLanguageChange`), and the
 * editor is re-created for the new content via the `modelValue` watch
 * below plus `setModelLanguage`. Applying the language in that same
 * watch, rather than in a second one, is what keeps the two from racing:
 * `setValue` alone resets the model to plain text, and the language has
 * to be re-declared immediately after it.
 *
 * **C1: this component is also the edit-behaviour probe.** It is the only
 * thing in the app that can see a paste — Monaco fires `onDidPaste` on
 * the editor instance and nowhere else — so it is where the system's
 * learning-process data actually originates. The recorder lives in
 * `edit-trace.ts`; this file only tells it what happened, and only in
 * *sizes*, never in text. See that module for why.
 *
 * **PU-17 added the breakpoint gutter.** Three props/events and one
 * decoration layer:
 *
 * * `breakpoints` — the line numbers to mark. The *parent* owns this; the
 *   editor renders it and reports clicks. One owner means the gutter and
 *   the sidecar cannot disagree, which matters because the sidecar is
 *   what gdb actually honours.
 * * `currentLine` — the line the debugger is stopped on, or `null`.
 * * `toggle-breakpoint` — emitted with the line number on a gutter click.
 *
 * The marker is drawn with `deltaDecorations` rather than by styling the
 * gutter DOM: Monaco owns that DOM and rewrites it on every scroll, so a
 * class applied by hand survives until the next repaint and then silently
 * disappears. Decorations are Monaco's own model and survive everything.
 *
 * `currentLine` gets a *line* decoration rather than a glyph one, so the
 * whole line is tinted — a student scanning for "where am I" looks at the
 * code, not at the 16px gutter.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import monaco from '../monaco'
import { editTrace } from '../edit-trace'
import { ALGOFORGE_THEME, installAlgoforgeTheme, refreshAlgoforgeTheme, type MonacoLanguageId } from '../monaco-theme'
import { themeVersion } from '../composables/useTheme'

// Importing this component is what installs the theme, so the two cannot
// be separated. Module scope, so it happens before any editor is created
// with `theme: ALGOFORGE_THEME`; a name Monaco does not know silently
// resolves to the light `vs`, which looks like the theme was ignored.
installAlgoforgeTheme()

const props = withDefaults(
  defineProps<{
    modelValue: string
    language?: MonacoLanguageId
    theme?: string
    /**
     * Lines to mark as breakpoints (PU-17). Owned by the parent, which is
     * also what tells the sidecar — see the component docstring.
     */
    breakpoints?: number[]
    /** The line execution is stopped on, or `null` when not debugging. */
    currentLine?: number | null
  }>(),
  {
    language: 'cpp',
    theme: ALGOFORGE_THEME,
    breakpoints: () => [],
    currentLine: null
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'toggle-breakpoint', line: number): void
}>()

const container = ref<HTMLDivElement | null>(null)
let editor: monaco.editor.IStandaloneCodeEditor | null = null

/**
 * Ids of the currently-applied decorations.
 *
 * Kept so the next call can retire exactly these rather than clearing the
 * whole layer — `deltaDecorations(old, new)` replaces by id, and passing
 * an empty old list would leave the previous markers behind as orphans.
 */
let decorationIds: string[] = []

/**
 * Repaint the breakpoint dots and the current-line highlight.
 *
 * Decorations are applied for lines beyond the end of the file too: a
 * student who sets a breakpoint and *then* deletes lines above it has a
 * breakpoint on a line that no longer exists, and the honest render is an
 * invisible marker that comes back if the line does. Filtering them here
 * would silently drop the student's intent.
 */
function renderDecorations(): void {
  if (!editor) return

  const decorations: monaco.editor.IModelDeltaDecoration[] = []

  for (const line of props.breakpoints) {
    decorations.push({
      range: new monaco.Range(line, 1, line, 1),
      options: {
        // `glyphMarginClassName` draws in the gutter strip rather than
        // over the line numbers, so the number stays readable.
        glyphMarginClassName: 'code-editor__breakpoint',
        glyphMarginHoverMessage: { value: '断点（调试时在这里停住）' },
        stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges
      }
    })
  }

  if (props.currentLine !== null && props.currentLine !== undefined) {
    decorations.push({
      range: new monaco.Range(props.currentLine, 1, props.currentLine, 1),
      options: {
        isWholeLine: true,
        className: 'code-editor__current-line',
        stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges
      }
    })
  }

  decorationIds = editor.deltaDecorations(decorationIds, decorations)
}

onMounted(() => {
  if (!container.value) return

  editor = monaco.editor.create(container.value, {
    value: props.modelValue,
    language: props.language,
    theme: props.theme,
    automaticLayout: true,
    fontSize: 14,
    minimap: { enabled: true },
    scrollBeyondLastLine: false,
    tabSize: 4,
    insertSpaces: false,
    // The strip the breakpoint dots are painted into. Without this the
    // glyph classes are applied to nothing and the gutter looks inert.
    glyphMargin: true
  })

  // Creating the editor is the start of an editing session: whatever was
  // on screen before belongs to a different problem, or to nothing.
  editTrace.reset(props.modelValue.length)

  editor.onDidChangeModelContent((e) => {
    if (!editor) return

    // `isFlush` is the buffer being replaced wholesale — `setValue` from
    // the watch below, i.e. a different problem or a language switch.
    // The trace has to start over, because what it has been describing
    // is no longer the code on screen.
    if (e.isFlush) {
      editTrace.reset(editor.getValue().length)
    } else {
      // Sizes only. This loop never touches the text of a change, so the
      // trace can never carry source code.
      let inserted = 0
      let removed = 0
      for (const change of e.changes) {
        inserted += change.text.length
        removed += change.rangeLength
      }
      editTrace.applyChange(inserted, removed, e.isUndoing || e.isRedoing)
    }

    emit('update:modelValue', editor.getValue())
  })

  /**
   * A click in the glyph strip toggles a breakpoint on that line.
   *
   * Checked against `GUTTER_GLYPH_MARGIN` specifically rather than
   * anywhere in the gutter: a click on the line *number* is Monaco's own
   * gesture (it selects the line), and hijacking it would break a
   * familiar editor behaviour to add a less discoverable one.
   */
  editor.onMouseDown((e) => {
    if (e.target.type !== monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN) return
    const line = e.target.position?.lineNumber
    if (line === undefined) return
    emit('toggle-breakpoint', line)
  })

  // Painted once at creation so a session restored with breakpoints
  // already set (or a remount after a tab switch) shows them.
  renderDecorations()

  // Fired *after* the content change above, from the same synchronous
  // call, which is why it rewrites the event just recorded rather than
  // adding one — see `markLastAsPaste` in `edit-trace.ts`.
  editor.onDidPaste(() => {
    editTrace.markPaste()
  })
})

/**
 * Keep the editor in sync when either the content or the language
 * changes externally.
 *
 * Both are handled in one watcher because they are one operation:
 * `setValue` replaces the model's text *and* its language association
 * (a fresh write resets tokenization), so `setModelLanguage` has to run
 * after it. Split across two watchers, a change that touched both props
 * in the same tick would leave whichever ran second deciding the
 * language — and the failure would look like "highlighting is sometimes
 * wrong" rather than a lost race.
 */
watch([() => props.modelValue, () => props.language], ([newValue, newLanguage]) => {
  if (!editor) return
  const model = editor.getModel()

  if (newValue !== editor.getValue()) {
    editor.setValue(newValue)
  }
  if (model) {
    monaco.editor.setModelLanguage(model, newLanguage)
  }
})

/**
 * Repaint when the markers change.
 *
 * Watches a *serialised* form rather than the arrays themselves. The
 * parent re-creates `breakpoints` on every render (it comes from a
 * `computed` over a `Set`), so a reference comparison would see a new
 * array each time and repaint on every unrelated render — while an edit
 * that produced an equal set would repaint for nothing. Comparing the
 * sorted contents is what makes "did the markers actually change?" the
 * question being asked.
 */
watch(
  () => [...props.breakpoints].sort((a, b) => a - b).join(','),
  () => renderDecorations()
)

watch(
  () => props.currentLine,
  () => renderDecorations()
)

/**
 * Re-apply the editor theme when the visual style switches.
 *
 * The AlgoForge theme reads its colours from CSS, so a `data-theme`
 * change means the literals differ. `refreshAlgoforgeTheme()`
 * re-resolves and redefines the theme; calling `setTheme` again is what
 * makes a *base* change (Paper uses `vs`, the dark themes use
 * `vs-dark`) take effect on the live editor — redefining alone leaves
 * the running editor on its original base.
 */
watch(themeVersion, () => {
  refreshAlgoforgeTheme()
  if (editor) monaco.editor.setTheme(props.theme)
})

onBeforeUnmount(() => {
  editor?.dispose()
  editor = null
  decorationIds = []
})
</script>

<template>
  <div ref="container" class="code-editor" />
</template>

<style scoped>
/*
 * No background here on purpose: Monaco paints its own, from
 * `editor.background` in the theme. A CSS background would show through
 * the gaps while the editor is initialising and make the theme look like
 * it is flickering.
 */
.code-editor {
  width: 100%;
  height: 100%;
  min-height: 300px;
}
</style>

<!--
  The breakpoint marker, deliberately in a *non-scoped* block.

  Monaco appends its gutter DOM to `document.body`-adjacent containers
  rather than to this component's root, so a `data-v-*` scoped attribute
  is never applied to it and a scoped rule would match nothing. This is
  the one case in the codebase where that is the right trade — the class
  names are prefixed `code-editor__` so they cannot collide.
-->
<style>
/*
 * A dot, drawn with a background circle rather than a glyph character:
 * a character depends on the font having it, and the editor's font is
 * configurable.
 */
.code-editor__breakpoint {
  background: radial-gradient(
    circle at center,
    var(--af-danger) 0 4px,
    transparent 4px
  );
  cursor: pointer;
}

/*
 * The tint on the stopped line. Low alpha on purpose: the line has to
 * read as "you are here" without competing with the syntax highlighting
 * the student is actually reading.
 */
.code-editor__current-line {
  background: rgb(79 140 255 / 0.16);
}
</style>
