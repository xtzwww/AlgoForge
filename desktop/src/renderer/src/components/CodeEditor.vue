<script setup lang="ts">
/**
 * CodeEditor — Monaco Editor wrapper for C++ code editing.
 *
 * Props:
 *   modelValue (string)   — current code content (v-model)
 *   language (string)     — editor language, defaults to "cpp"
 *   theme (string)        — editor theme, defaults to "vs-dark"
 *
 * Emits:
 *   update:modelValue     — fired when the code changes
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import monaco from '../monaco'

const props = withDefaults(
  defineProps<{
    modelValue: string
    language?: string
    theme?: string
  }>(),
  {
    language: 'cpp',
    theme: 'vs-dark',
  },
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
    insertSpaces: false,
  })

  editor.onDidChangeModelContent(() => {
    if (editor) {
      emit('update:modelValue', editor.getValue())
    }
  })
})

// Keep the editor content in sync when modelValue changes externally
watch(
  () => props.modelValue,
  (newVal) => {
    if (editor && editor.getValue() !== newVal) {
      editor.setValue(newVal)
    }
  },
)

onBeforeUnmount(() => {
  editor?.dispose()
  editor = null
})
</script>

<template>
  <div ref="container" class="code-editor" />
</template>

<style scoped>
.code-editor {
  width: 100%;
  height: 100%;
  min-height: 300px;
}
</style>
