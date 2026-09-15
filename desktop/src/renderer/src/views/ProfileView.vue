<script setup lang="ts">
/**
 * ProfileView — the ability profile page (P1-22).
 *
 * Layout:
 *   ┌── Header: 标题 + 刷新按钮 ───────────────────────────┐
 *   ├── Radar chart (掌握度雷达图) ────────────────────────┤
 *   │   各知识点正确率的多边形可视化                          │
 *   ├────────────────────────────────────────────────────────┤
 *   │  Stats table (详细统计表)                              │
 *   │   tag | total | passed | accuracy | progress bar      │
 *   └────────────────────────────────────────────────────────┘
 *
 * Data flow:
 *   onMounted → getRadar() + getKnowledgeStats() → feed into
 *   RadarChart props + table rows.
 */
import { computed, onMounted, ref } from 'vue'
import RadarChart from '../components/RadarChart.vue'
import { getKnowledgeStats, getRadar, type KnowledgeStat } from '../api/profile'

const loading = ref(false)
const errorMsg = ref('')
const stats = ref<KnowledgeStat[]>([])
const radarIndicators = ref<{ name: string; max: number }[]>([])
const radarValues = ref<number[]>([])

/** Stats sorted by accuracy ascending — weakest topics first. */
const sortedStats = computed<KnowledgeStat[]>(() => {
  return [...stats.value].sort((a, b) => a.accuracy - b.accuracy)
})

/** Overall accuracy across all submissions. */
const overallAccuracy = computed<number>(() => {
  if (stats.value.length === 0) return 0
  const total = stats.value.reduce((sum, s) => sum + s.total, 0)
  if (total === 0) return 0
  const passed = stats.value.reduce((sum, s) => sum + s.passed, 0)
  return passed / total
})

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    const [statsResp, radarResp] = await Promise.all([
      getKnowledgeStats(),
      getRadar(),
    ])
    stats.value = statsResp
    radarIndicators.value = radarResp.indicators
    radarValues.value = radarResp.values
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

function formatPercent(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

function accuracyColor(accuracy: number): string {
  if (accuracy >= 0.8) return '#22c55e'
  if (accuracy >= 0.6) return '#eab308'
  if (accuracy >= 0.4) return '#f97316'
  return '#ef4444'
}

onMounted(load)
</script>

<template>
  <div class="profile-view">
    <header class="header">
      <h1>能力画像</h1>
      <button class="refresh-btn" :disabled="loading" @click="load">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
    </header>

    <!-- Overall summary -->
    <div class="summary">
      <div class="summary-card">
        <span class="summary-label">总正确率</span>
        <span class="summary-value" :style="{ color: accuracyColor(overallAccuracy) }">
          {{ formatPercent(overallAccuracy) }}
        </span>
      </div>
      <div class="summary-card">
        <span class="summary-label">知识点数</span>
        <span class="summary-value">{{ stats.length }}</span>
      </div>
      <div class="summary-card">
        <span class="summary-label">总题数</span>
        <span class="summary-value">
          {{ stats.reduce((sum, s) => sum + s.total, 0) }}
        </span>
      </div>
    </div>

    <!-- Error message -->
    <div v-if="errorMsg" class="error-msg">
      请求失败: {{ errorMsg }}
    </div>

    <!-- Radar chart -->
    <section class="section">
      <h2 class="section-title">掌握度雷达图</h2>
      <RadarChart
        :indicators="radarIndicators"
        :values="radarValues"
        :loading="loading"
      />
    </section>

    <!-- Stats table -->
    <section class="section">
      <h2 class="section-title">详细统计</h2>
      <div v-if="!loading && sortedStats.length === 0 && !errorMsg" class="empty">
        暂无提交记录。完成一些题目后再来看看能力画像。
      </div>
      <table v-else-if="sortedStats.length > 0" class="stats-table">
        <thead>
          <tr>
            <th>知识点</th>
            <th>总数</th>
            <th>通过</th>
            <th>正确率</th>
            <th>掌握度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sortedStats" :key="s.tag">
            <td class="tag-cell">{{ s.tag }}</td>
            <td>{{ s.total }}</td>
            <td>{{ s.passed }}</td>
            <td :style="{ color: accuracyColor(s.accuracy) }">
              {{ formatPercent(s.accuracy) }}
            </td>
            <td>
              <div class="progress-bar">
                <div
                  class="progress-fill"
                  :style="{
                    width: `${s.accuracy * 100}%`,
                    background: accuracyColor(s.accuracy),
                  }"
                />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.profile-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding: 16px;
  box-sizing: border-box;
  color: #e5e7eb;
  background: #1a1a2e;
  overflow-y: auto;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.header h1 {
  margin: 0;
  font-size: 22px;
}

.refresh-btn {
  padding: 6px 16px;
  font-size: 13px;
  color: white;
  background: #3b82f6;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.refresh-btn:disabled {
  background: #6b7280;
  cursor: not-allowed;
}

.summary {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px;
  background: #16213e;
  border-radius: 8px;
}

.summary-label {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 4px;
}

.summary-value {
  font-size: 24px;
  font-weight: bold;
}

.error-msg {
  color: #ef4444;
  padding: 8px 12px;
  background: #7f1d1d;
  border-radius: 4px;
  margin-bottom: 12px;
}

.section {
  margin-bottom: 24px;
}

.section-title {
  font-size: 16px;
  margin: 0 0 12px 0;
  color: #e5e7eb;
}

.empty {
  color: #6b7280;
  text-align: center;
  padding: 40px 16px;
  font-size: 14px;
  background: #16213e;
  border-radius: 8px;
}

.stats-table {
  width: 100%;
  border-collapse: collapse;
  background: #16213e;
  border-radius: 8px;
  overflow: hidden;
}

.stats-table th,
.stats-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid #1e293b;
}

.stats-table th {
  font-size: 12px;
  color: #9ca3af;
  font-weight: normal;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stats-table td {
  font-size: 14px;
}

.tag-cell {
  font-weight: bold;
  color: #93c5fd;
}

.progress-bar {
  width: 120px;
  height: 8px;
  background: #374151;
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  transition: width 0.3s ease;
}
</style>
