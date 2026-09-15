<script setup lang="ts">
/**
 * PlanView — the review plan page (P1-23).
 *
 * Layout:
 *   ┌── Header: 标题 + 刷新按钮 ───────────────────────────┐
 *   ├── Today's review list ──────────────────────────────┤
 *   │   Entry card:                                         │
 *   │     entry_id · repetitions · last_reviewed_at         │
 *   │     "今天到期" badge                                    │
 *   │     quality buttons: [0 黑屏] [1 错误] [2 错误]       │
 *   │                      [3 艰难] [4 犹豫] [5 完美]       │
 *   │     → after marking, shows next review date           │
 *   └────────────────────────────────────────────────────────┘
 *
 * Workflow:
 *   1. onMounted → listDueReviews() → today's due items.
 *   2. User clicks a quality button (0-5).
 *   3. recordReview(entryId, quality) → SM-2 recomputes.
 *   4. The item is removed from the "today" list (its due_at moved
 *      into the future), and a toast shows the next review date.
 */
import { computed, onMounted, ref } from 'vue'
import {
  listDueReviews,
  recordReview,
  type ReviewState,
} from '../api/review'
import { getMistake, type MistakeEntry } from '../api/mistake_book'

const dueReviews = ref<ReviewState[]>([])
const loading = ref(false)
const errorMsg = ref('')
/** Cache of mistake-book entries for display, keyed by entry_id. */
const entryCache = ref<Map<number, MistakeEntry>>(new Map())
/** Toast message for "next review" confirmation. */
const toast = ref<{ entryId: number; dueAt: string } | null>(null)

/** Quality score options for the buttons. */
const QUALITY_OPTIONS: { value: number; label: string; color: string }[] = [
  { value: 0, label: '黑屏', color: '#7c2d12' },
  { value: 1, label: '错误', color: '#9a3412' },
  { value: 2, label: '错误', color: '#b91c1c' },
  { value: 3, label: '艰难', color: '#ca8a04' },
  { value: 4, label: '犹豫', color: '#16a34a' },
  { value: 5, label: '完美', color: '#15803d' },
]

const dueCount = computed<number>(() => dueReviews.value.length)

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    const items = await listDueReviews()
    dueReviews.value = items
    // Pre-fetch mistake-book entries for display.
    await Promise.all(
      items.map(async (r) => {
        if (!entryCache.value.has(r.entry_id)) {
          try {
            const entry = await getMistake(r.entry_id)
            entryCache.value.set(r.entry_id, entry)
          } catch {
            // Entry may have been deleted; skip silently.
          }
        }
      }),
    )
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

async function markQuality(entryId: number, quality: number): Promise<void> {
  try {
    const updated = await recordReview(entryId, quality)
    // Remove the item from the today list (its due_at moved forward).
    dueReviews.value = dueReviews.value.filter((r) => r.entry_id !== entryId)
    // Show toast with the next review date.
    const dueAt = updated.due_at ? formatDate(updated.due_at) : '未知'
    toast.value = { entryId, dueAt }
    setTimeout(() => {
      if (toast.value?.entryId === entryId) toast.value = null
    }, 4000)
  } catch (err) {
    errorMsg.value = (err as Error).message
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

function getEntry(entryId: number): MistakeEntry | undefined {
  return entryCache.value.get(entryId)
}

onMounted(load)
</script>

<template>
  <div class="plan-view">
    <header class="header">
      <h1>复习计划</h1>
      <button class="refresh-btn" :disabled="loading" @click="load">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
    </header>

    <!-- Today summary -->
    <div class="summary">
      <span class="summary-text">
        今日待复习: <strong>{{ dueCount }}</strong> 项
      </span>
    </div>

    <!-- Error message -->
    <div v-if="errorMsg" class="error-msg">
      请求失败: {{ errorMsg }}
    </div>

    <!-- Toast -->
    <div v-if="toast" class="toast">
      已记录复习。下次复习时间: <strong>{{ toast.dueAt }}</strong>
    </div>

    <!-- Empty state -->
    <div v-if="!loading && dueCount === 0 && !errorMsg" class="empty">
      <template v-if="dueReviews.length === 0">
        今日无待复习项。保持练习，错题会自动进入复习队列。
      </template>
    </div>

    <!-- Due review list -->
    <div v-if="dueCount > 0" class="review-list">
      <article
        v-for="r in dueReviews"
        :key="r.entry_id"
        class="review-card"
      >
        <div class="review-header">
          <span class="review-title">
            题目 #{{ getEntry(r.entry_id)?.problem_id ?? '?' }}
          </span>
          <span class="badge badge-due">今天到期</span>
        </div>

        <p v-if="getEntry(r.entry_id)?.error_cause" class="review-cause">
          {{ getEntry(r.entry_id)?.error_cause }}
        </p>
        <p v-else class="review-cause placeholder">
          （尚未填写错因）
        </p>

        <div class="review-tags" v-if="getEntry(r.entry_id)?.knowledge_tags.length">
          <span
            v-for="tag in getEntry(r.entry_id)?.knowledge_tags"
            :key="tag"
            class="review-tag"
          >
            {{ tag }}
          </span>
        </div>

        <div class="review-meta">
          <span class="meta-item">已复习 {{ r.repetitions }} 次</span>
          <span class="meta-item">上次复习: {{ formatDateTime(r.last_reviewed_at) }}</span>
          <span class="meta-item">EF: {{ r.easiness_factor.toFixed(2) }}</span>
        </div>

        <div class="quality-section">
          <span class="quality-label">复习质量:</span>
          <div class="quality-buttons">
            <button
              v-for="opt in QUALITY_OPTIONS"
              :key="opt.value"
              class="quality-btn"
              :style="{ background: opt.color }"
              @click="markQuality(r.entry_id, opt.value)"
            >
              <span class="quality-num">{{ opt.value }}</span>
              <span class="quality-text">{{ opt.label }}</span>
            </button>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.plan-view {
  display: flex;
  flex-direction: column;
  /* PU-6: was 100vh. The app shell owns the viewport now and stacks a title
   * bar above this view, so the page must fill its container instead. */
  height: 100%;
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
  margin-bottom: 12px;
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
  margin-bottom: 16px;
  padding: 12px 16px;
  background: #16213e;
  border-radius: 8px;
}

.summary-text {
  font-size: 15px;
}

.summary-text strong {
  color: #3b82f6;
  font-size: 18px;
}

.error-msg {
  color: #ef4444;
  padding: 8px 12px;
  background: #7f1d1d;
  border-radius: 4px;
  margin-bottom: 12px;
}

.toast {
  position: fixed;
  top: 60px;
  right: 24px;
  padding: 12px 20px;
  background: #14532d;
  color: #86efac;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  z-index: 100;
  animation: slideIn 0.3s ease;
}

@keyframes slideIn {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

.empty {
  color: #6b7280;
  text-align: center;
  padding: 60px 16px;
  font-size: 14px;
  background: #16213e;
  border-radius: 8px;
}

.review-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.review-card {
  padding: 16px;
  background: #16213e;
  border-radius: 8px;
  border-left: 4px solid #f59e0b;
}

.review-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.review-title {
  font-weight: bold;
  font-size: 15px;
}

.badge {
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 10px;
}

.badge-due {
  background: #78350f;
  color: #fbbf24;
}

.review-cause {
  margin: 8px 0;
  font-size: 14px;
  line-height: 1.5;
  color: #e5e7eb;
}

.review-cause.placeholder {
  color: #6b7280;
  font-style: italic;
}

.review-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0;
}

.review-tag {
  padding: 2px 8px;
  font-size: 12px;
  color: #93c5fd;
  background: #1e3a5f;
  border-radius: 4px;
}

.review-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 12px;
}

.quality-section {
  border-top: 1px solid #1e293b;
  padding-top: 12px;
}

.quality-label {
  display: block;
  font-size: 13px;
  color: #9ca3af;
  margin-bottom: 8px;
}

.quality-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.quality-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 6px 12px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: transform 0.1s, opacity 0.2s;
  min-width: 60px;
}

.quality-btn:hover {
  transform: translateY(-2px);
  opacity: 0.9;
}

.quality-num {
  font-size: 16px;
  font-weight: bold;
  color: white;
}

.quality-text {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.8);
}
</style>
