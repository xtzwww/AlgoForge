<script setup lang="ts">
/**
 * 题面（描述 / 输入输出格式 / 知识点标签 / 样例 / 时限）。
 *
 * 从 `Practice.vue`（1247 行）里抽出来，理由与 `AttentionAlerts.vue` 一样：
 * 它**不持有状态、不发请求**，父组件把要显示的东西推进来，它只负责画。
 * 这是这个 1247 行的视图里唯一能脱离编辑器与判题流单独被测的一块。
 *
 * 值得钉住的同样是「坏了只是该出现的没出现」那类行为：
 *
 * - **样例的输入为空时要显示「(无)」。** 空输入是一道合法用例（"读入直到 EOF"
 *   或纯打印题），显示成空白会被读成「这道题没有输入」，学生照着写就 WA。
 * - **没有知识点标签时整行不渲染**，而不是渲染一个空的行占位。
 * - **自由练习与题库模式共用这一个组件**，靠 `isProblemMode` 切换「样例 /
 *   用例」的措辞——措辞差异是刻意的：题库里给的是公开样例，隐藏用例要提交
 *   才跑；自由练习里那几个就是全部用例。把措辞说错，学生会以为自己看见了全部。
 */
import type { TestCaseIn } from '../api/judge'

defineProps<{
  description: string
  /** Empty when the problem has no input-format section. */
  inputFormat: string
  outputFormat: string
  knowledgeTags: string[]
  cases: TestCaseIn[]
  /** true = 题库模式（公开样例）；false = 自由练习（这几个就是全部用例）。 */
  isProblemMode: boolean
  timeLimit: number | null
  memoryLimit: number | null
}>()
</script>

<template>
  <pre v-if="description" class="prose">{{ description }}</pre>

  <template v-if="inputFormat">
    <h2>输入格式</h2>
    <pre class="prose">{{ inputFormat }}</pre>
  </template>
  <template v-if="outputFormat">
    <h2>输出格式</h2>
    <pre class="prose">{{ outputFormat }}</pre>
  </template>

  <div v-if="knowledgeTags.length" class="tag-row">
    <span v-for="tag in knowledgeTags" :key="tag" class="tag">
      {{ tag }}
    </span>
  </div>

  <h2>{{ isProblemMode ? '样例' : '测试用例' }}</h2>
  <div v-for="(tc, i) in cases" :key="i" class="sample">
    <div class="sample-label">{{ isProblemMode ? '样例' : '用例' }} {{ i + 1 }}</div>
    <div class="sample-row">
      <span class="sample-key">输入</span>
      <code>{{ tc.input || '(无)' }}</code>
    </div>
    <div class="sample-row">
      <span class="sample-key">输出</span>
      <code>{{ tc.expected_output }}</code>
    </div>
  </div>

  <p v-if="isProblemMode" class="hint">
    隐藏用例由后端持有，<strong>提交</strong>时会一并执行；<strong>运行</strong>只跑上面的公开样例。
  </p>
  <p v-if="timeLimit !== null && memoryLimit !== null" class="hint">
    时限 {{ timeLimit }}s · 内存 {{ memoryLimit }} MB
  </p>
</template>

<style scoped>
/*
  Moved here with the markup — `Practice.vue` is `<style scoped>`, and a
  scoped rule reaches only a child component's root element, so every one
  of these would have silently stopped applying to the inner nodes.
*/
.prose {
  margin: 0;
  padding: 10px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border-radius: var(--af-radius-sm);
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.tag {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-accent);
  background: var(--af-accent-subtle);
  border-radius: 999px;
}

.sample {
  padding: 10px;
  margin-bottom: 8px;
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.sample-label {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-secondary);
}

.sample-row {
  display: flex;
  gap: 8px;
  margin: 3px 0;
  font-size: 12px;
}

.sample-key {
  flex-shrink: 0;
  width: 32px;
  color: var(--af-text-muted);
}

.sample-row code {
  padding: 1px 5px;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border-radius: 3px;
}

.hint {
  margin: 12px 0 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.hint strong {
  color: var(--af-text-secondary);
}
</style>
