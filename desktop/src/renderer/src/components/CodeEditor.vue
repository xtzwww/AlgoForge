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
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import monaco from '../monaco'
import { ALGOFORGE_THEME, installAlgoforgeTheme, type MonacoLanguageId } from '../monaco-theme'

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
  }>(),
  {
    language: 'cpp',
    theme: ALGOFORGE_THEME
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const container = ref<HTMLDivElement | null>(null)
let editor: monaco.editor.IStandaloneCodeEditor | null = null

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
    insertSpaces: false
  })

  editor.onDidChangeModelContent(() => {
    if (editor) {
      emit('update:modelValue', editor.getValue())
    }
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

onBeforeUnmount(() => {
  editor?.dispose()
  editor = null
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
