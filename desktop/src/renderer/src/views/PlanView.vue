<script setup lang="ts">
/**
 * PlanView — the review plan (P1-23, rewritten in PU-9).
 *
 * Layout:
 *   ┌── PageHeader: 复习计划 · 待复习计数 · 刷新 ───────────┐
 *   ├── Review card:                                        │
 *   │     题目 #id · 今天到期                                │
 *   │     error_cause · tags                                 │
 *   │     已复习 n 次 · 上次复习 · EF                        │
 *   │     复习质量: [0 黑屏] … [5 完美]                       │
 *   │     → after scoring: 下次复习 in N 天                   │
 *   └────────────────────────────────────────────────────────┘
 *
 * Workflow:
 *   1. onMounted → listDueReviews() → today's due items.
 *   2. User clicks a quality button (0-5).
 *   3. recordReview(entryId, quality) → SM-2 recomputes server-side.
 *   4. The item leaves the today list and the card reports the
 *      interval the server just computed.
 *
 * **PU-9 changed four things.**
 *
 * 1. *The quality scale is explained.* The buttons said 黑屏/错误/错误/
 *    艰难/犹豫/完美 with no statement of what the numbers mean or where
 *    3 sits. The 0–5 scale is not self-evident — 3 is the pass/fail
 *    boundary in SM-2, which is the single most important thing to know
 *    when pressing these buttons, because everything below it resets the
 *    streak. The panel now says so, and the boundary is marked in the row.
 *
 * 2. *The optimistic removal is now reversible on failure.* Pre-PU-9
 *    `markQuality` removed the card from the list and *then* called the
 *    API; a failed call left the card gone and an error message behind
 *    it, with the entry still overdue on the next refresh. The card now
 *    stays and shows its own in-flight state.
 *
 * 3. *The next-interval feedback replaces a floating toast.* The toast
 *    was `position: fixed` at a hardcoded `top: 60px` — wrong the moment
 *    the topbar height changed — and it lived outside the card that
 *    caused it. The result now appears inside the card as an outcome
 *    line, which is also what makes it readable by the next refresh.
 *
 * 4. *The empty state offers a way out*, and the error no longer renders
 *    beside it.
 *
 * ---------------------------------------------------------------------
 * **Reviewing updates two separate things, and this page only owns one.**
 *
 * `POST /review/{id}/review` advances the SM-2 row; it does **not** set
 * `MistakeBookEntry.reviewed`. Those are different flags with different
 * meanings — `reviewed` is "the user has looked at this since it was
 * archived", `ReviewRecord` is the schedule — and the mistake book's own
 * 标记已复习 button is what writes the former. This page deliberately
 * leaves the entry flag alone: a single quality press already sends one
 * request, and firing a second PATCH at the entry endpoint would make a
 * mis-scored review harder to undo than it should be.
 *
 * The consequence is that a user can score the same entry many times over
 * successive intervals while it still reads 未复习 in the mistake book.
 * That is correct, not a bug: they are two different questions.
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import { listDueReviews, recordReview, type ReviewState } from '../api/review'
import { getMistake, type MistakeEntry } from '../api/mistake_book'

const router = useRouter()

const dueReviews = ref<ReviewState[]>([])
const loading = ref(false)
const errorMsg = ref('')
/** Cache of mistake-book entries for display, keyed by entry_id. */
const entryCache = ref<Map<number, MistakeEntry>>(new Map())

/**
 * The entry ids with a review request in flight. A `Set` rather than a
 * single id: the list is a queue and a user can score several cards in
 * turn, so "which buttons are busy" is per-card state.
 */
const submitting = ref<Set<number>>(new Set())

/**
 * The last scored entry's outcome, keyed by entry id.
 *
 * Kept in a map rather than a single value so a card that has just been
 * scored keeps its own result visible while the *next* card is being
 * scored — the pre-PU-9 toast could only describe one item at a time and
 * was overwritten by the next press.
 */
const outcomes = ref<Map<number, { dueAt: string; intervalDays: number; quality: number }>>(
  new Map()
)

/**
 * Quality scores and what each means for the schedule.
 *
 * `label` is the short button caption; `hint` is the full sentence shown
 * in the legend. `resets` marks the fail band — quality < 3 is where SM-2
 * resets the repetition streak, so it is the one boundary worth drawing a
 * line under.
 */
const QUALITY_OPTIONS: {
  value: number
  label: string
  hint: string
  /** Band tone, resolved to a token in the template. */
  tone: 'fail' | 'hard' | 'good'
}[] = [
  { value: 0, label: '黑屏', hint: '完全想不起来', tone: 'fail' },
  { value: 1, label: '错误', hint: '看过答案才想起', tone: 'fail' },
  { value: 2, label: '错误', hint: '想起一部分，仍答错', tone: 'fail' },
  { value: 3, label: '艰难', hint: '费了很大劲才答对', tone: 'hard' },
  { value: 4, label: '犹豫', hint: '稍有迟疑，但答对', tone: 'good' },
  { value: 5, label: '完美', hint: '毫不犹豫答对', tone: 'good' }
]

const TONE_VARS: Record<string, string> = {
  fail: 'var(--af-danger)',
  hard: 'var(--af-warning)',
  good: 'var(--af-success)'
}

const dueCount = computed<number>(() => dueReviews.value.length)

/** Entries that have already been scored in this session. */
const scoredCount = computed<number>(() => outcomes.value.size)

const subtitle = computed<string>(() => {
  if (dueCount.value === 0 && scoredCount.value === 0) return ''
  const parts = [`待复习 ${dueCount.value} 项`]
  if (scoredCount.value > 0) parts.push(`本次已复习 ${scoredCount.value} 项`)
  return parts.join(' · ')
})

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    const items = await listDueReviews()
    dueReviews.value = items

    // Pre-fetch the mistake entries the cards display. Deliberately not
    // awaited in a way that can fail the page: an entry may have been
    // deleted under us, and a card with no title is better than no page.
    const cache = new Map(entryCache.value)
    await Promise.all(
      items.map(async (r) => {
        if (!cache.has(r.entry_id)) {
          try {
            cache.set(r.entry_id, await getMistake(r.entry_id))
          } catch {
            // Entry deleted; the card falls back to "题目 #?".
          }
        }
      })
    )
    entryCache.value = cache
  } catch (err) {
    errorMsg.value = (err as Error).message
    dueReviews.value = []
  } finally {
    loading.value = false
  }
}

async function markQuality(state: ReviewState, quality: number): Promise<void> {
  const entryId = state.entry_id
  if (submitting.value.has(entryId)) return
  submitting.value = new Set(submitting.value).add(entryId)

  try {
    const updated = await recordReview(entryId, quality)

    /*
     * The outcome is recorded and the card is *kept* in the list — it is
     * no longer due, but removing it would erase the only evidence that
     * the press did anything. `load()` on the next visit drops it, which
     * is the honest test of whether the schedule moved.
     */
    dueReviews.value = dueReviews.value.map((r) =>
      r.entry_id === entryId ? { ...r, ...updated } : r
    )

    const next = new Map(outcomes.value)
    next.set(entryId, {
      dueAt: formatDate(updated.due_at),
      intervalDays: updated.interval_days,
      quality
    })
    outcomes.value = next
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    const remaining = new Set(submitting.value)
    remaining.delete(entryId)
    submitting.value = remaining
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  })
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

/** Relative phrasing for the outcome line: "3 天后" beats a date. */
function intervalLabel(days: number): string {
  if (days <= 0) return '今天'
  if (days === 1) return '明天'
  return `${days} 天后`
}

function getEntry(entryId: number): MistakeEntry | undefined {
  return entryCache.value.get(entryId)
}

/**
 * The outcome for a card, or `undefined` if it has not been scored.
 *
 * A function rather than reading the map in the template: the template
 * needs four fields off the same object, and four `outcomes.get(id)!`
 * calls mean four non-null assertions that each re-assert a fact
 * established once. Reading it here makes the `v-if` and the values it
 * guards agree by construction — they are the same lookup.
 */
function outcomeOf(
  entryId: number
): { dueAt: string; intervalDays: number; quality: number } | undefined {
  return outcomes.value.get(entryId)
}

function goToMistakes(): void {
  void router.push({ name: 'mistakes' })
}

onMounted(load)
</script>

<template>
  <div class="plan-view">
    <div class="content">
      <PageHeader
        title="复习计划"
        :subtitle="subtitle"
        :loading="loading"
        :error="errorMsg"
        @refresh="load"
      />

      <!-- Loading, first paint only. -->
      <div v-if="loading && dueReviews.length === 0" class="placeholder">加载复习计划中…</div>

      <EmptyState
        v-else-if="!errorMsg && dueCount === 0"
        icon="calendar"
        :title="scoredCount > 0 ? '今日复习已经清空' : '今天没有待复习的题目'"
        :explanation="
          scoredCount > 0
            ? '刚评分的题目已经按记忆曲线排到以后了。明天再来看看。'
            : '复习队列由错题本驱动：判题未通过的提交会自动归档并排进这里。还没有错题时，先去练习页提交一次。'
        "
        :action-label="scoredCount > 0 ? '' : '去错题本看看'"
        @navigate="goToMistakes"
      />

      <div v-else-if="dueCount > 0" class="review-list">
        <!-- The scale explained once, above the cards, rather than
             repeated inside each one. -->
        <div class="scale-note">
          <p class="scale-title">怎么打分</p>
          <p class="scale-body">
            诚实地回忆，然后按困难程度选择 0–5。<strong>3 分是关键分界</strong>：低于 3
            分表示这次没想起来，记忆曲线会重置并从明天重新开始；3 分及以上才会把复习间隔拉长。
          </p>
        </div>

        <article
          v-for="r in dueReviews"
          :key="r.entry_id"
          class="review-card"
          :class="{ busy: submitting.has(r.entry_id), scored: outcomes.has(r.entry_id) }"
        >
          <div class="review-header">
            <span class="review-title"> 题目 #{{ getEntry(r.entry_id)?.problem_id ?? '?' }} </span>
            <span v-if="outcomes.has(r.entry_id)" class="badge badge-done">本次已复习</span>
            <span v-else class="badge badge-due">今天到期</span>
          </div>

          <p v-if="getEntry(r.entry_id)?.error_cause" class="review-cause">
            {{ getEntry(r.entry_id)?.error_cause }}
          </p>
          <p v-else class="review-cause review-cause-empty">尚未填写错因 · 待 AI 分析</p>

          <div v-if="getEntry(r.entry_id)?.knowledge_tags.length" class="review-tags">
            <span v-for="tag in getEntry(r.entry_id)?.knowledge_tags" :key="tag" class="review-tag">
              {{ tag }}
            </span>
          </div>

          <div class="review-meta">
            <span class="meta-item">已复习 {{ r.repetitions }} 次</span>
            <span class="meta-item">上次复习 {{ formatDateTime(r.last_reviewed_at) }}</span>
            <span class="meta-item" title="SM-2 难度系数，越低说明越难记住">
              EF {{ r.easiness_factor.toFixed(2) }}
            </span>
          </div>

          <!-- Outcome: what the last press actually did. -->
          <div v-if="outcomeOf(r.entry_id)" class="outcome">
            <span class="outcome-icon" aria-hidden="true">✓</span>
            <span>
              已记录 {{ outcomeOf(r.entry_id)!.quality }} 分，下次复习
              <strong>{{ intervalLabel(outcomeOf(r.entry_id)!.intervalDays) }}</strong>
              <span class="outcome-date">（{{ outcomeOf(r.entry_id)!.dueAt }}）</span>
            </span>
          </div>

          <div class="quality-section">
            <span class="quality-label">复习质量：</span>
            <div class="quality-buttons">
              <template v-for="(opt, i) in QUALITY_OPTIONS" :key="opt.value">
                <!-- The fail/pass boundary. Drawn between buttons 2 and 3
                     rather than as a label on one of them, because it is a
                     relationship between them. -->
                <span
                  v-if="i > 0 && QUALITY_OPTIONS[i - 1].tone === 'fail' && opt.tone !== 'fail'"
                  class="quality-divider"
                  aria-hidden="true"
                />
                <button
                  class="quality-btn"
                  type="button"
                  :disabled="submitting.has(r.entry_id)"
                  :style="{ '--tone': TONE_VARS[opt.tone] }"
                  :title="opt.hint"
                  @click="markQuality(r, opt.value)"
                >
                  <span class="quality-num">{{ opt.value }}</span>
                  <span class="quality-text">{{ opt.label }}</span>
                </button>
              </template>
            </div>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
/*
 * No hex literals. The per-band quality colours arrive as the `--tone`
 * custom property bound inline from `TONE_VARS`, which is how a token
 * reaches a pseudo-state (border, hover) that an inline style cannot
 * express directly.
 */
.plan-view {
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

/* --- scale explanation --- */
.scale-note {
  padding: 12px 14px;
  margin-bottom: 12px;
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.scale-title {
  margin: 0 0 4px 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.scale-body {
  margin: 0;
  font-size: 12px;
  line-height: 1.65;
  color: var(--af-text-secondary);
}

.scale-body strong {
  color: var(--af-warning);
}

/* --- list --- */
.review-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.review-card {
  position: relative;
  padding: 14px 16px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  transition:
    opacity var(--af-duration-base) var(--af-ease),
    border-color var(--af-duration-base) var(--af-ease);
}

/* Same inset-bar technique as the mistake book — see that file for why
 * `border-left` was avoided. Amber means "due"; green once scored. */
.review-card::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 3px;
  background: var(--af-warning);
  border-radius: var(--af-radius-md) 0 0 var(--af-radius-md);
}

.review-card.scored::before {
  background: var(--af-success);
}

.review-card.busy {
  opacity: 0.6;
}

.review-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 8px;
}

.review-title {
  font-size: 15px;
  font-weight: 600;
}

.badge {
  padding: 1px 8px;
  font-size: 11px;
  border-radius: 999px;
  white-space: nowrap;
}

.badge-due {
  color: var(--af-warning);
  background: color-mix(in srgb, var(--af-warning) 18%, transparent);
}

.badge-done {
  color: var(--af-success);
  background: color-mix(in srgb, var(--af-success) 18%, transparent);
}

.review-cause {
  margin: 8px 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--af-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.review-cause-empty {
  font-style: italic;
  color: var(--af-text-muted);
}

.review-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0;
}

.review-tag {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-accent);
  background: var(--af-accent-subtle);
  border-radius: 999px;
}

.review-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--af-text-muted);
}

/* --- outcome line --- */
.outcome {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 8px 10px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--af-text-primary);
  background: color-mix(in srgb, var(--af-success) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-success) 35%, transparent);
  border-radius: var(--af-radius-sm);
}

.outcome-icon {
  color: var(--af-success);
  font-weight: 700;
}

.outcome strong {
  color: var(--af-success);
  font-variant-numeric: tabular-nums;
}

.outcome-date {
  color: var(--af-text-muted);
  font-size: 12px;
}

/* --- quality buttons --- */
.quality-section {
  padding-top: 12px;
  border-top: 1px solid var(--af-border);
}

.quality-label {
  display: block;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--af-text-secondary);
}

.quality-buttons {
  display: flex;
  align-items: stretch;
  flex-wrap: wrap;
  gap: 6px;
}

/* The 2|3 fail/pass boundary; `align-self: stretch` makes it follow the
 * button height without a magic number. */
.quality-divider {
  align-self: stretch;
  width: 1px;
  margin: 2px 3px;
  background: var(--af-border-strong);
}

.quality-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  min-width: 58px;
  padding: 5px 10px;
  font-family: inherit;
  /* The band tone, with the surface as a faint wash so six buttons do not
   * read as six saturated blocks fighting for attention. */
  color: var(--tone);
  background: color-mix(in srgb, var(--tone) 16%, var(--af-surface));
  border: 1px solid color-mix(in srgb, var(--tone) 45%, transparent);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    transform var(--af-duration-fast) var(--af-ease);
}

.quality-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--tone) 30%, var(--af-surface));
}

.quality-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.quality-num {
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.quality-text {
  font-size: 11px;
  color: var(--af-text-secondary);
  white-space: nowrap;
}

@media (prefers-reduced-motion: reduce) {
  .quality-btn,
  .review-card,
  .quality-btn:hover:not(:disabled) {
    transition: none;
  }
}
</style>
