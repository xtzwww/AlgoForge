<script setup lang="ts">
/**
 * 变式题练习页（2026-09-27，#5 变式题闭环）。
 *
 * 错题本里 S7 的变式题过去只展示、没地方做：题干、样例、知识点都在，
 * 但没有编辑器、没有判题，学生看完了这道"举一反三"就没了下文。这个
 * 页面是那道缺掉的门——从错题本里「用编辑器做这道题」进来，用变式题
 * 自带的样例当场判 AC/WA。
 *
 * 为什么判题走 inline cases 而不是题库的 problem_id：
 *   变式题是模型现场写的，没有 problem 行、没有隐藏用例。它的测试集
 *   就是它自带的 `sample_input` / `sample_output`——服务端 judge 端点
 *   本来就支持 caller-supplied cases（自测路径），直接复用，不新增后端。
 *
 * 为什么没有 AI 辅助面板：
 *   变式题的本质是"把你刚错的那道题的某一个旋钮拧了一下"（difference
 *   字段）。它是对着错因做的自我检验，不是要攻克的新题——开辅助档位
 *   会让老师给这份作业设的强度失去意义，也会把"举一反三"变成"举一反
 *   三但可以求助"。保持简单：一个编辑器 + 一个判题。
 *
 * 运行与提交的差别（PU-8 的 record 语义）：运行是草稿（不落库、不计
 * 分），提交才写历史。这里两条路都给：学生先跑样例确认自己理解对了，
 * 再提交让这次练习真正留痕。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NSelect } from 'naive-ui'
import CodeEditor from '../components/CodeEditor.vue'
import VerdictBadge from '../components/VerdictBadge.vue'
import { getMistake, type MistakeEntry } from '../api/mistake_book'
import { judgeCodeWithCases, type JudgeResponse } from '../api/judge'
import { describeRequestErrorText } from '../api/errors'
import {
  getLanguageOptions,
  pickDefaultLanguage,
  type LanguageOption
} from '../api/languages'
import type { MonacoLanguageId } from '../monaco-theme'
import type { SelectOption } from 'naive-ui'

const route = useRoute()
const router = useRouter()

const entryId = Number(route.query.entry)
const variantIndex = Number(route.query.index)

const loading = ref(true)
const errorMsg = ref('')
const entry = ref<MistakeEntry | null>(null)

const code = ref('')
const language = ref<MonacoLanguageId>('cpp')
const languageOptions = ref<LanguageOption[]>([])
const judgeBusy = ref(false)
const judgeError = ref('')
const result = ref<JudgeResponse | null>(null)

const variant = computed(() => {
  const e = entry.value
  if (!e) return null
  const v = e.variants[variantIndex]
  return v ?? null
})

const languageSelectOptions = computed<SelectOption[]>(() =>
  languageOptions.value
    .filter((o) => o.available)
    .map((o) => ({ label: o.display_name, value: o.id }))
)

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    entry.value = await getMistake(entryId)
    if (!variant.value) {
      errorMsg.value = '这道变式题不存在，可能已被删除。'
    } else {
      code.value = ''
      result.value = null
    }
  } catch (err) {
    errorMsg.value = describeRequestErrorText(err, '无法加载这道变式题')
  } finally {
    loading.value = false
  }
}

function runKind(kind: 'run' | 'submit'): void {
  const v = variant.value
  if (!v || judgeBusy.value) return
  judgeBusy.value = true
  judgeError.value = ''
  void judgeCodeWithCases(
    code.value,
    [{ input: v.sample_input, expected_output: v.sample_output }],
    { language: language.value, record: kind === 'submit' }
  )
    .then((res) => {
      result.value = res
    })
    .catch((err) => {
      judgeError.value = describeRequestErrorText(err, '无法判定')
    })
    .finally(() => {
      judgeBusy.value = false
    })
}

function backToMistakes(): void {
  void router.push({ name: 'mistakes' })
}

onMounted(async () => {
  try {
    languageOptions.value = await getLanguageOptions()
    const preferred = pickDefaultLanguage(languageOptions.value)
    if (preferred) language.value = preferred.id as MonacoLanguageId
  } catch {
    // 拿不到语言表就退回默认 C++：编辑器仍能编辑，判题交给服务端校验。
  }
  await load()
})
</script>

<template>
  <div class="variant-page">
    <div class="variant-head">
      <NButton quaternary size="small" @click="backToMistakes">← 返回错题本</NButton>
      <span class="variant-page-title">变式题练习</span>
    </div>

    <p v-if="loading" class="variant-placeholder">加载中…</p>

    <p v-else-if="errorMsg" class="variant-error">{{ errorMsg }}</p>

    <div v-else-if="variant" class="variant-layout">
      <!-- 左：题目。样例必须原样呈现（空白敏感）。 -->
      <div class="variant-statement-pane">
        <p class="variant-statement">{{ variant.statement }}</p>

        <div class="variant-samples">
          <div class="variant-sample">
            <span class="variant-sample-key">输入</span>
            <pre class="variant-sample-body">{{ variant.sample_input }}</pre>
          </div>
          <div class="variant-sample">
            <span class="variant-sample-key">输出</span>
            <pre class="variant-sample-body">{{ variant.sample_output }}</pre>
          </div>
        </div>

        <div v-if="variant.knowledge_points.length" class="variant-points">
          <span v-for="p in variant.knowledge_points" :key="p" class="variant-point">
            {{ p }}
          </span>
        </div>

        <p v-if="variant.difference" class="variant-diff">
          <span class="variant-diff-key">与原题的区别</span>{{ variant.difference }}
        </p>
      </div>

      <!-- 右：编辑器 + 判题。 -->
      <div class="variant-editor-pane">
        <div class="editor-toolbar">
          <NSelect
            class="lang-select"
            size="small"
            :value="language"
            :options="languageSelectOptions"
            @update:value="(v) => (language = String(v) as MonacoLanguageId)"
          />
          <NButton size="small" :loading="judgeBusy" @click="runKind('run')">运行</NButton>
          <NButton size="small" type="primary" :loading="judgeBusy" @click="runKind('submit')">
            提交
          </NButton>
        </div>

        <div class="variant-code-editor">
          <CodeEditor v-model="code" :language="language" />
        </div>

        <p v-if="judgeError" class="variant-error">{{ judgeError }}</p>

        <div v-if="result" class="variant-verdict">
          <div class="verdict-line">
            <VerdictBadge :status="result.status" />
            <span class="verdict-passed">
              通过 {{ result.passed }} / {{ result.total }}
            </span>
          </div>
          <pre v-if="result.compile_output" class="compile-output">{{
            result.compile_output
          }}</pre>
          <div v-for="(tc, i) in result.test_cases" :key="i" class="case-row">
            <span class="case-status">{{ tc.status }}</span>
            <div class="case-body">
              <p v-if="tc.actual_output !== undefined" class="case-line">
                你的输出：
              </p>
              <pre class="case-output">{{ tc.actual_output }}</pre>
              <p v-if="tc.status !== 'AC'" class="case-line">期望输出：</p>
              <pre v-if="tc.status !== 'AC'" class="case-output">{{
                tc.expected_output
              }}</pre>
              <p v-if="tc.stderr" class="case-line stderr">{{ tc.stderr }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.variant-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px;
  overflow-y: auto;
}

.variant-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.variant-page-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--af-text);
}

.variant-placeholder,
.variant-error {
  font-size: 13px;
  color: var(--af-text-secondary);
}

.variant-error {
  color: var(--af-danger, #ef4444);
}

.variant-layout {
  display: grid;
  grid-template-columns: minmax(300px, 380px) 1fr;
  gap: 16px;
  align-items: start;
}

.variant-statement-pane {
  padding: 14px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.variant-statement {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.variant-samples {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}

.variant-sample-key {
  display: block;
  margin-bottom: 2px;
  font-size: 11px;
  color: var(--af-text-muted);
}

.variant-sample-body {
  margin: 0;
  padding: 6px 8px;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-secondary);
  background: var(--af-bg);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  white-space: pre-wrap;
}

.variant-points {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.variant-point {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-text-secondary);
  background: var(--af-surface);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
}

.variant-diff {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

.variant-diff-key {
  margin-right: 6px;
  font-size: 11px;
  color: var(--af-text-muted);
}

.variant-editor-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.editor-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.lang-select {
  width: 140px;
}

.variant-code-editor {
  min-height: 420px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  overflow: hidden;
}

.variant-verdict {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.verdict-line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.verdict-passed {
  font-size: 13px;
  color: var(--af-text-secondary);
}

.compile-output {
  margin: 0;
  padding: 8px 10px;
  font-size: 12px;
  white-space: pre-wrap;
  color: var(--af-text-secondary);
  background: var(--af-bg);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.case-row {
  padding: 8px 10px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.case-status {
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-secondary);
}

.case-body {
  margin-top: 4px;
}

.case-line {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--af-text-muted);
}

.case-line.stderr {
  color: var(--af-danger, #ef4444);
}

.case-output {
  margin: 0;
  padding: 4px 6px;
  font-size: 12px;
  white-space: pre-wrap;
  color: var(--af-text-secondary);
  background: var(--af-bg);
  border-radius: var(--af-radius-sm);
}
</style>
