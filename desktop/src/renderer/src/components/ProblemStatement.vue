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
  <!--
    正文用 <div class="prose">（无衬线）而不是 <pre>。
    <pre> 会被 tokens.css 的全局规则归入等宽字体栈，中文在等宽字体下
    笔画粗细不均、字距松散，"读起来累"就是这么来的。题面正文是
    给人读的中文散文，不是代码 —— 需要保序的换行由 white-space:
    pre-wrap 单独给，不必为了换行把字体也换成等宽。
  -->
  <div v-if="description" class="prose">{{ description }}</div>

  <template v-if="inputFormat">
    <h2>输入格式</h2>
    <div class="prose">{{ inputFormat }}</div>
  </template>
  <template v-if="outputFormat">
    <h2>输出格式</h2>
    <div class="prose">{{ outputFormat }}</div>
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
  padding: 12px 14px;
  /*
   14px / 1.8：正文是中文散文，行距要比界面文本宽 —— 1.5 是给标签
   和按钮的密度，一整段题面按那个行距读十几行会累。
   font-family 显式写一遍无衬线栈：这一块曾经是 <pre>，被全局等宽
   规则接管；改回无衬线是这次排版修复的核心，不能只靠继承 body。
  */
  font-family:
    -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB',
    'Microsoft YaHei', sans-serif;
  font-size: 14px;
  line-height: 1.8;
  letter-spacing: 0.01em;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

/* 空段落不占位：题面里常有多余空行，渲染成一整块空白很难看。 */
.prose:empty {
  display: none;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 14px;
}

.tag {
  padding: 2px 9px;
  font-size: 11px;
  color: var(--af-accent);
  background: var(--af-accent-subtle);
  border-radius: 999px;
}

.sample {
  padding: 12px 14px;
  margin-bottom: 10px;
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-left: 3px solid var(--af-accent);
  border-radius: var(--af-radius-sm);
}

.sample-label {
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--af-text-secondary);
}

.sample-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 5px 0;
  font-size: 12.5px;
}

.sample-key {
  flex-shrink: 0;
  width: 32px;
  padding-top: 1px;
  color: var(--af-text-muted);
}

/* 样例的输入输出保留等宽：那确实是数据，对齐比笔画匀称更重要。 */
.sample-row code {
  flex: 1;
  min-width: 0;
  padding: 3px 7px;
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border-radius: 4px;
}

.hint {
  margin: 14px 0 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--af-text-muted);
}

.hint strong {
  color: var(--af-text-secondary);
}
</style>
