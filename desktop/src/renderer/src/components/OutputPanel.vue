<script setup lang="ts">
/**
 * OutputPanel — displays the streamed judge progress (compile/case
 * events) and the final aggregated result.
 *
 * P1-16 extracts this from Practice.vue so the parent owns the state
 * and this component stays purely presentational.
 */
import type { JudgeEvent } from '../api/ws'
import type { JudgeResponse } from '../api/judge'

defineProps<{
  events: JudgeEvent[]
  result: JudgeResponse | null
  errorMsg: string
  isRunning: boolean
}>()

function statusColor(status: string): string {
  switch (status) {
    case 'AC':
      return '#22c55e'
    case 'WA':
      return '#ef4444'
    case 'TLE':
      return '#f59e0b'
    case 'MLE':
      return '#06b6d4'
    case 'CE':
      return '#8b5cf6'
    case 'RE':
      return '#f97316'
    default:
      return '#9ca3af'
  }
}

function eventLabel(type: string): string {
  switch (type) {
    case 'compile_start':
      return '开始编译'
    case 'compile_done':
      return '编译完成'
    case 'case_start':
      return '用例开始'
    case 'case_done':
      return '用例完成'
    case 'judge_done':
      return '判题完成'
    case 'judge_error':
      return '错误'
    default:
      return type
  }
}
</script>

<template>
  <div class="output-panel">
    <!-- Streamed progress events -->
    <div v-if="events.length > 0" class="event-stream">
      <h3>判题进度</h3>
      <div v-for="(ev, i) in events" :key="i" class="event-row">
        <span class="event-label" :style="{ color: ev.type === 'judge_error' ? '#ef4444' : '#9ca3af' }">
          {{ eventLabel(ev.type) }}
        </span>
        <span v-if="ev.type === 'compile_done'" class="event-detail" :style="{ color: ev.success ? '#22c55e' : '#ef4444' }">
          {{ ev.success ? '成功' : '失败' }}
        </span>
        <span v-if="ev.type === 'case_start'" class="event-detail">
          #{{ (ev.index ?? 0) + 1 }}
        </span>
        <span v-if="ev.type === 'case_done'" class="event-detail" :style="{ color: statusColor(ev.status ?? '') }">
          #{{ (ev.index ?? 0) + 1 }} → {{ ev.status }}
        </span>
        <span v-if="ev.type === 'judge_done'" class="event-detail" :style="{ color: statusColor(ev.status ?? '') }">
          {{ ev.status }} ({{ ev.passed }}/{{ ev.total }})
        </span>
        <span v-if="ev.type === 'judge_error'" class="event-detail" style="color: #ef4444">
          {{ ev.error }}
        </span>
      </div>
    </div>

    <!-- Error message -->
    <div v-if="errorMsg" class="error-msg">
      请求失败: {{ errorMsg }}
    </div>

    <!-- Final result -->
    <div v-if="result" class="result-panel">
      <div class="result-summary">
        <span class="result-status" :style="{ color: statusColor(result.status) }">
          {{ result.status }}
        </span>
        <span class="result-count">
          通过 {{ result.passed }} / {{ result.total }}
        </span>
      </div>

      <div v-if="result.compile_output" class="compile-output">
        <h3>编译输出</h3>
        <pre>{{ result.compile_output }}</pre>
      </div>

      <div v-for="(tc, i) in result.test_cases" :key="i" class="tc-result">
        <div class="tc-result-header">
          <span class="tc-result-status" :style="{ color: statusColor(tc.status) }">
            {{ tc.status }}
          </span>
          <span class="tc-result-label">用例 {{ i + 1 }}</span>
        </div>
        <div v-if="tc.status === 'TLE'" class="tc-info">运行超时</div>
        <div v-else-if="tc.status === 'MLE'" class="tc-info">内存超限</div>
        <div v-else-if="tc.status !== 'AC' && tc.status !== 'CE'" class="tc-diff">
          <div class="tc-diff-row">
            <span class="tc-key">期望:</span>
            <code>{{ tc.expected_output }}</code>
          </div>
          <div class="tc-diff-row">
            <span class="tc-key">实际:</span>
            <code>{{ tc.actual_output }}</code>
          </div>
        </div>
      </div>
    </div>

    <div v-if="isRunning && events.length === 0 && !errorMsg" class="placeholder">
      等待判题事件…
    </div>
  </div>
</template>

<style scoped>
.output-panel {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  background: #16213e;
  border-radius: 8px;
  padding: 12px;
}

.event-stream {
  margin-bottom: 12px;
}

.event-stream h3 {
  font-size: 14px;
  margin: 0 0 8px 0;
}

.event-row {
  font-size: 13px;
  margin: 4px 0;
  display: flex;
  gap: 10px;
  align-items: center;
}

.event-label {
  min-width: 70px;
}

.event-detail {
  font-weight: 600;
}

.error-msg {
  color: #ef4444;
  padding: 8px;
  background: #7f1d1d;
  border-radius: 4px;
  margin-bottom: 8px;
}

.result-panel {
  margin-top: 12px;
}

.result-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 12px;
}

.result-status {
  font-size: 28px;
  font-weight: bold;
}

.result-count {
  font-size: 16px;
  color: #d1d5db;
}

.compile-output h3 {
  font-size: 14px;
  margin: 8px 0 4px 0;
}

.compile-output pre {
  background: #0f172a;
  padding: 8px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

.tc-result {
  margin-bottom: 10px;
  padding: 8px;
  background: #0f3460;
  border-radius: 4px;
}

.tc-result-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.tc-result-status {
  font-weight: bold;
  font-size: 16px;
}

.tc-result-label {
  color: #9ca3af;
}

.tc-info {
  font-size: 13px;
  color: #d1d5db;
}

.tc-diff {
  font-size: 13px;
}

.tc-diff-row {
  margin: 2px 0;
}

.tc-key {
  color: #9ca3af;
  margin-right: 6px;
}

.placeholder {
  color: #6b7280;
  font-size: 13px;
  padding: 12px;
  text-align: center;
}
</style>
