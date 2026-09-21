<script setup lang="ts">
/**
 * ProfileView — the ability profile (P1-22, rewritten in PU-9).
 *
 * Layout:
 *   ┌── PageHeader: 能力画像 · 刷新 ─────────────────────────┐
 *   ├── Summary cards: 总正确率 · 知识点数 · 总提交数 ────────┤
 *   ├── Radar (掌握度雷达图) ────────────────────────────────┤
 *   ├── Stats table: tag | total | passed | accuracy | bar   │
 *   └────────────────────────────────────────────────────────┘
 *
 * **PU-9 changed three things.**
 *
 * 1. *`accuracyColor()` is gone.* It returned one of four hex literals
 *    and was called from three places — the summary figure, the table
 *    cell and the progress bar — so the page held the mastery ramp in
 *    four copies of the same thresholds. The ramp is now
 *    `masteryColorFor()` in `assets/tokens.ts`, and the radar reads the
 *    matching `--af-mastery-*` tokens, so the chart's fill and the
 *    table's bars cannot disagree about what 0.65 looks like.
 *
 * 2. *A 「数据不足」 state.* See below.
 *
 * 3. *The empty state offers a way out* and the error no longer renders
 *    beside it — nor does the rest of the page. `load()` clears `stats` on
 *    failure, so letting the body render would print 「总正确率 0.0% ·
 *    0 / 0 次通过」, which states a fact the student does not have. The
 *    failure is the state; the header's error banner plus its refresh
 *    button is the whole response. `MistakesView` already reads this way
 *    (its list hangs off an independent `v-if`); this page had the body
 *    on the tail `v-else` of the loading/empty chain, so it slipped in.
 *
 * ---------------------------------------------------------------------
 * **Why the radar needs a minimum sample size.**
 *
 * The pre-PU-9 radar drew whatever `GET /profile/radar` returned. With
 * one submission the polygon becomes a single spike to 0% or 100% on one
 * axis; with two it is a triangle. Both look like a chart, and a chart
 * reads as information — a user seeing a dramatic spike has no way to
 * know it is one attempt, not a measured weakness. The radar's whole
 * claim is that the polygon's *shape* means something, and the shape of
 * n < 3 points means nothing.
 *
 * So below three submissions the page says so instead of drawing. The
 * threshold is on total submissions rather than per-tag attempts, because
 * the axis labels come from the whole submission set and it is the
 * *overall* picture that is being withheld — the table underneath still
 * shows every tag's raw numbers, which is the honest place to look at n=1.
 *
 * It counts `submissions`, not `stats.length`: a tag with one submission
 * is one submission, while `stats.length` counts tags. The two would
 * diverge the moment a tag appears with zero attempts.
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import RadarChart from '../components/RadarChart.vue'
import { masteryColorFor } from '../assets/tokens'
import { getKnowledgeStats, getRadar, type KnowledgeStat } from '../api/profile'

/** Below this many submissions the radar's shape is not meaningful. */
const MIN_SUBMISSIONS_FOR_RADAR = 3

const router = useRouter()

const loading = ref(false)
const errorMsg = ref('')
const stats = ref<KnowledgeStat[]>([])
const radarIndicators = ref<{ name: string; max: number }[]>([])
const radarValues = ref<number[]>([])

/** Stats sorted by accuracy ascending — weakest topics first. */
const sortedStats = computed<KnowledgeStat[]>(() =>
  [...stats.value].sort((a, b) => a.accuracy - b.accuracy)
)

/** Total submissions across every knowledge tag. */
const totalSubmissions = computed<number>(() => stats.value.reduce((sum, s) => sum + s.total, 0))

/** Total passes, for the same denominator. */
const totalPassed = computed<number>(() => stats.value.reduce((sum, s) => sum + s.passed, 0))

/**
 * Overall accuracy across all submissions.
 *
 * Weighted by attempts rather than an average of per-tag accuracies: a
 * tag with one lucky attempt should not pull the figure as hard as a tag
 * with fifty.
 */
const overallAccuracy = computed<number>(() => {
  if (totalSubmissions.value === 0) return 0
  return totalPassed.value / totalSubmissions.value
})

/**
 * Whether there is enough data for the radar to be meaningful. See the
 * docstring — this is about the sample size, not about whether a request
 * succeeded.
 */
const hasEnoughForRadar = computed<boolean>(
  () => totalSubmissions.value >= MIN_SUBMISSIONS_FOR_RADAR
)

/** How many more submissions the radar needs, for the copy. */
const submissionsNeeded = computed<number>(() => MIN_SUBMISSIONS_FOR_RADAR - totalSubmissions.value)

const subtitle = computed<string>(() => {
  if (totalSubmissions.value === 0) return ''
  return `共 ${totalSubmissions.value} 次提交 · ${stats.value.length} 个知识点`
})

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    const [statsResp, radarResp] = await Promise.all([getKnowledgeStats(), getRadar()])
    stats.value = statsResp
    radarIndicators.value = radarResp.indicators
    radarValues.value = radarResp.values
  } catch (err) {
    errorMsg.value = (err as Error).message
    stats.value = []
    radarIndicators.value = []
    radarValues.value = []
  } finally {
    loading.value = false
  }
}

function formatPercent(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

function accuracyColor(accuracy: number): string {
  return masteryColorFor(accuracy)
}

function goToProblems(): void {
  void router.push({ name: 'problems' })
}

onMounted(load)
</script>

<template>
  <div class="profile-view">
    <div class="content">
      <PageHeader
        title="能力画像"
        :subtitle="subtitle"
        :loading="loading"
        :error="errorMsg"
        @refresh="load"
      />

      <div v-if="loading && stats.length === 0" class="placeholder">加载画像中…</div>

      <EmptyState
        v-else-if="!errorMsg && stats.length === 0"
        icon="chart"
        title="还没有可分析的数据"
        explanation="画像根据你的判题结果统计各知识点的正确率。去题库挑一道题提交一次，这里就会出现第一个数据点。"
        action-label="去题库选题"
        @navigate="goToProblems"
      />

      <template v-else-if="!errorMsg">
        <!-- Summary -->
        <div class="summary">
          <div class="summary-card">
            <span class="summary-label">总正确率</span>
            <span class="summary-value" :style="{ color: accuracyColor(overallAccuracy) }">
              {{ formatPercent(overallAccuracy) }}
            </span>
            <span class="summary-hint"> {{ totalPassed }} / {{ totalSubmissions }} 次通过 </span>
          </div>
          <div class="summary-card">
            <span class="summary-label">知识点数</span>
            <span class="summary-value">{{ stats.length }}</span>
            <span class="summary-hint">已接触</span>
          </div>
          <div class="summary-card">
            <span class="summary-label">总提交数</span>
            <span class="summary-value">{{ totalSubmissions }}</span>
            <span class="summary-hint">含重复提交</span>
          </div>
        </div>

        <!-- Radar -->
        <section class="section">
          <h2 class="section-title">掌握度雷达图</h2>

          <!--
            The 数据不足 state. Rendered *instead of* the chart rather than
            as an overlay on it — see the docstring for why a misleading
            polygon is worse than no polygon.
          -->
          <div v-if="!hasEnoughForRadar" class="radar-insufficient">
            <svg
              class="insufficient-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M3 3v18h18M7 15l4-5 3 3 5-7" />
            </svg>
            <p class="insufficient-title">数据不足，暂不绘制雷达图</p>
            <p class="insufficient-body">
              已记录 <strong>{{ totalSubmissions }}</strong> 次提交，至少需要
              <strong>{{ MIN_SUBMISSIONS_FOR_RADAR }}</strong> 次才能看出知识点之间的相对强弱。
              再提交 {{ submissionsNeeded }} 次即可解锁。
            </p>
            <p class="insufficient-note">
              下面表格里的原始数字仍然有效——样本少时看数字比看图形可靠。
            </p>
          </div>

          <RadarChart
            v-else
            :indicators="radarIndicators"
            :values="radarValues"
            :loading="loading"
            empty-title="暂无知识点数据"
            empty-explanation="题目的知识点标签为空时无法生成雷达图。"
          />
        </section>

        <!-- Stats table -->
        <section v-if="sortedStats.length > 0" class="section">
          <h2 class="section-title">详细统计</h2>
          <!-- Weakest first, with the reason stated: the order is a
               recommendation, and an unexplained sort looks arbitrary. -->
          <p class="section-note">按正确率升序排列，最需要补的知识点在最上面。</p>

          <div class="table-scroll">
            <table class="stats-table">
              <thead>
                <tr>
                  <th>知识点</th>
                  <th class="num">总数</th>
                  <th class="num">通过</th>
                  <th class="num">正确率</th>
                  <th>掌握度</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in sortedStats" :key="s.tag">
                  <td class="tag-cell">{{ s.tag }}</td>
                  <td class="num">{{ s.total }}</td>
                  <td class="num">{{ s.passed }}</td>
                  <td class="num" :style="{ color: accuracyColor(s.accuracy) }">
                    {{ formatPercent(s.accuracy) }}
                  </td>
                  <td>
                    <div
                      class="progress-bar"
                      role="img"
                      :aria-label="`${s.tag} 掌握度 ${formatPercent(s.accuracy)}`"
                    >
                      <div
                        class="progress-fill"
                        :style="{
                          width: `${Math.max(s.accuracy * 100, 2)}%`,
                          background: accuracyColor(s.accuracy)
                        }"
                      />
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
/*
 * No hex literals: `accuracyColor()` resolves through `masteryColorFor()`
 * in tokens.ts, and every static colour here is a `--af-*`.
 */
.profile-view {
  height: 100%;
  overflow-y: auto;
  background: var(--af-canvas);
  color: var(--af-text-primary);
}

.content {
  max-width: var(--af-content-max);
  margin: 0 auto;
  padding: 20px 16px 32px 16px;
  box-sizing: border-box;
}

.placeholder {
  padding: 40px 16px;
  font-size: 14px;
  text-align: center;
  color: var(--af-text-muted);
}

/* --- summary --- */
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.summary-card {
  flex: 1 1 160px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 14px 12px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.summary-label {
  font-size: 12px;
  color: var(--af-text-secondary);
}

.summary-value {
  font-size: 24px;
  font-weight: 600;
  /* Stops the three cards' figures shifting as digits change width. */
  font-variant-numeric: tabular-nums;
}

.summary-hint {
  font-size: 11px;
  color: var(--af-text-muted);
}

/* --- sections --- */
.section {
  margin-bottom: 24px;
}

.section-title {
  margin: 0 0 8px 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.section-note {
  margin: 0 0 10px 0;
  font-size: 12px;
  color: var(--af-text-muted);
}

/* --- insufficient state --- */
.radar-insufficient {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 240px;
  padding: 24px;
  text-align: center;
  background: var(--af-surface);
  border: 1px dashed var(--af-border-strong);
  border-radius: var(--af-radius-md);
}

.insufficient-icon {
  width: 30px;
  height: 30px;
  margin-bottom: 2px;
  color: var(--af-text-muted);
}

.insufficient-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.insufficient-body {
  max-width: 52ch;
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

.insufficient-body strong {
  color: var(--af-accent);
  font-variant-numeric: tabular-nums;
}

.insufficient-note {
  margin: 4px 0 0 0;
  font-size: 12px;
  color: var(--af-text-muted);
}

/* --- table --- */
/* The table can exceed a narrow viewport; scrolling it here keeps the
 * page's own scroll container from gaining a horizontal axis. */
.table-scroll {
  overflow-x: auto;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.stats-table {
  width: 100%;
  border-collapse: collapse;
}

.stats-table th,
.stats-table td {
  padding: 9px 12px;
  text-align: left;
  border-bottom: 1px solid var(--af-border);
}

.stats-table tbody tr:last-child td {
  border-bottom: none;
}

.stats-table thead th {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--af-text-muted);
}

.stats-table td {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.stats-table .num {
  text-align: right;
  white-space: nowrap;
}

.tag-cell {
  font-weight: 600;
  color: var(--af-text-primary);
}

.progress-bar {
  width: 120px;
  height: 8px;
  overflow: hidden;
  background: var(--af-elevated);
  border-radius: 999px;
}

.progress-fill {
  height: 100%;
  border-radius: 999px;
  transition: width var(--af-duration-base) var(--af-ease);
}

@media (max-width: 640px) {
  .progress-bar {
    width: 72px;
  }
}
</style>
