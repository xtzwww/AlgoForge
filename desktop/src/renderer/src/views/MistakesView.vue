<script setup lang="ts">
/**
 * MistakesView — the mistake book (P1-21, rewritten in PU-9).
 *
 * Layout:
 *   ┌── PageHeader: 错题本 · 计数 · 刷新 ────────────────────┐
 *   ├── Filter bar ─────────────────────────────────────────┤
 *   │  knowledge tags: [全部] [dp] [贪心] …                  │
 *   │  error-cause:    [错因 ▾]  ← P2-21                     │
 *   ├────────────────────────────────────────────────────────┤
 *   │  Entry card: #id · created_at · reviewed badge          │
 *   │              error_cause · tags · meta                  │
 *   │              [标记已复习] [删除]                          │
 *   └────────────────────────────────────────────────────────┘
 *
 * **PU-9 changed four things.**
 *
 * 1. *Every colour is a PU-0 token.* The old stylesheet carried
 *    `#1a1a2e`, `#16213e`, `#3b82f6`, `#ef4444` and friends; the file now
 *    holds exactly one literal, documented below. The reviewed /
 *    not-reviewed distinction used two fixed colours per state, which is
 *    four literals for one bit of state — it is now the shared
 *    `VerdictBadge`'s neutral-vs-success pair, so this page agrees with
 *    the practice page about what "green" means.
 *
 * 2. *The empty state offers a way out.* It used to read "错题本为空。
 *    判题失败后，可在练习页面将错题加入错题本。" — an accurate sentence
 *    that leaves the user to find the page themselves. It is now an
 *    `EmptyState` with a 去练习题 button, and the same component covers
 *    the "your tag filter matched nothing" case with flatter styling.
 *
 * 3. *An error no longer masquerades as emptiness.* Pre-PU-9 the failure
 *    banner rendered above the list while the empty state rendered
 *    below it, so a failed load showed both "请求失败" and "错题本为空"
 *    at once. The failure now lives in the `PageHeader` and the body
 *    renders nothing at all, because there is nothing to say.
 *
 * 4. *A reserved slot for the error-cause filter* (see below).
 *
 * ---------------------------------------------------------------------
 * **The 错因 filter (P2-21).**
 *
 * PU-14 reserved the slot and PU-9 deliberately shipped it as a
 * *disabled* control labelled 「AI 接入后可用」 rather than omitting it:
 * a user who cannot find a control concludes the app never had one,
 * while a user who sees it greyed out with a reason learns that the
 * feature is a *later* thing rather than a *broken* thing. That was the
 * honest state while `agent_error_type` was NULL on every row.
 *
 * It is no longer honest — P2-4 has been filling the label since the
 * analysis was wired up, so the control is now the real thing: a
 * `<select>` over the causes present in the loaded entries, labelled
 * with the Chinese wording the student is taught (`error_type_description`)
 * rather than the raw machine label. The 「AI 接入后可用」 note is gone
 * with it, because it would now be false.
 *
 * Two consequences of it being real, both deliberate:
 *
 * * *The options are the causes actually present*, exactly like the tag
 *   bar above it — not the full taxonomy. A filter that can be set to a
 *   value no entry has is a filter that answers "nothing" to a question
 *   the user did not ask.
 * * *It renders only when there is something to filter by.* The old slot
 *   was always visible because it was teaching; a working control with a
 *   single 「全部错因」 option teaches nothing and is a dead control,
 *   which is the thing the old design was avoiding in the first place.
 *   A mistake book whose entries have never been analysed therefore shows
 *   no cause filter, and that is the truthful rendering.
 *
 * The two filters compose (AND): 「dp」+「整数溢出」 narrows to entries
 * that are both. The empty state names whichever combination is active
 * and offers to clear all of it.
 *
 * ---------------------------------------------------------------------
 * **Layered feedback release (current phase).**
 *
 * This supersedes the "AI 诊断" block that used to render
 * `agent_suggestion` as one wall of text. The analysis has always
 * produced three separately-labelled sections — 错在哪 / 提示 / 怎么改 —
 * and rendering them together guaranteed only the last was read. The
 * page now releases them one at a time behind a 还是不会 button, starting
 * from nothing at all. See `LayeredFeedback.vue` for the full rationale;
 * the short version is that reading the fix is not the same as
 * understanding the error, and the UI was making the first one free.
 *
 * This is also why `agent_suggestion` is no longer used for display: the
 * API serves the three sections separately (`error_detail` / `hint` /
 * `suggested_fix`), and the joined column is kept only for callers with
 * no interest in pacing the reveal.
 *
 * ---------------------------------------------------------------------
 * **Filtering is client-side.** All entries are loaded once and filtered
 * in a computed. The server can filter by `problem_id`, `reviewed` and
 * `error_type` (P2-21 added the last one), none of which this page uses,
 * and a filter would otherwise be a request per chip click against a
 * corpus that is tens of entries at this stage.
 *
 * The cause filter is client-side for a second, stronger reason: its
 * *options* are derived from the loaded entries, so a server-side filter
 * would have to refetch before it could even know what to offer — and the
 * tag bar, whose options are derived the same way, would silently change
 * its chips under the user every time the cause changed. `GET
 * /api/v1/mistake-book?error_type=` exists for callers that hold no list
 * (and is tested), but this page is not one of them.
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import EmptyState from '../components/EmptyState.vue'
import LayeredFeedback from '../components/LayeredFeedback.vue'
import PageHeader from '../components/PageHeader.vue'
import {
  analyzeMistake,
  deleteMistake,
  getMistake,
  listMistakes,
  updateMistake,
  type FeedbackSection,
  type MistakeEntry
} from '../api/mistake_book'

const router = useRouter()

const entries = ref<MistakeEntry[]>([])
const loading = ref(false)
const errorMsg = ref('')
/** `null` = 全部. */
const selectedTag = ref<string | null>(null)

/**
 * The selected root cause (P2-21), by its machine label. `''` = 全部.
 *
 * Empty string rather than `null` — unlike {@link selectedTag}, which is
 * set by a chip's click handler and never round-trips through the DOM,
 * this one is the value of a `<select>`, and a select's values are
 * strings. `''` is the standard HTML spelling of "no choice", so the
 * binding needs no `:value` object trick to survive the round trip.
 *
 * Kept as the *label* rather than as the option object so a reload cannot
 * leave the selection pointing at a stale copy of a description — the
 * label is the identity and the wording is looked up fresh.
 */
const selectedCause = ref<string>('')

/**
 * What the review action is doing, per entry id.
 *
 * PU-9 adds this because 标记已复习 had no in-flight state: on a slow
 * sidecar the button stayed enabled and a double click sent two PATCHes,
 * the second of which a user would notice as the entry briefly
 * un-disappearing from the filtered view. `Set` rather than a single id
 * because two rows can legitimately be in flight at once.
 */
const pending = ref<Set<number>>(new Set())

/**
 * Entry ids whose retry is being polled (P2-24).
 *
 * Kept apart from {@link pending} because the two mean different things:
 * `pending` is one request in flight, while a retry is a background job
 * that can run for minutes. Sharing the set would leave 标记已复习
 * disabled for the whole analysis.
 */
const reanalysing = ref<Set<number>>(new Set())

/** All unique tags across all loaded entries, sorted. */
const allTags = computed<string[]>(() => {
  const set = new Set<string>()
  for (const e of entries.value) {
    for (const t of e.knowledge_tags) set.add(t)
  }
  return Array.from(set).sort()
})

/**
 * The error causes present in the loaded entries (P2-21).
 *
 * Derived from the data, not from the taxonomy in `app/eval_cases.py`:
 * this page has no copy of that list, and the tag bar next to it already
 * works this way. The Chinese wording comes from the API
 * (`error_type_description`), which is also where the "unknown label"
 * fallback lives — an entry whose label this build does not recognise
 * would otherwise be an unlabelled option.
 */
const allCauses = computed<{ label: string; description: string }[]>(() => {
  const descriptions = new Map<string, string>()
  for (const entry of entries.value) {
    const label = entry.agent_error_type
    if (!label || descriptions.has(label)) continue
    descriptions.set(label, entry.error_type_description || label)
  }
  return Array.from(descriptions, ([label, description]) => ({ label, description })).sort(
    (a, b) => a.description.localeCompare(b.description, 'zh-CN')
  )
})

/** The student-facing wording for a label, or the label when unknown. */
function causeDescription(label: string): string {
  return allCauses.value.find((c) => c.label === label)?.description ?? label
}

/**
 * Entries narrowed by both filters (AND). Either being `null` means "all".
 *
 * Composing rather than overriding: 「dp」+「整数溢出」 is a question a
 * student actually asks ("my overflow mistakes on DP problems"), and
 * letting one filter silently replace the other would answer a different
 * one.
 */
const filteredEntries = computed<MistakeEntry[]>(() =>
  entries.value.filter(
    (entry) =>
      (selectedTag.value === null ||
        entry.knowledge_tags.includes(selectedTag.value)) &&
      (selectedCause.value === '' || entry.agent_error_type === selectedCause.value)
  )
)

/** Whatever is narrowing the list right now, as text for the empty state. */
const activeFilterLabel = computed<string>(() => {
  const parts: string[] = []
  if (selectedTag.value !== null) parts.push(`「${selectedTag.value}」`)
  if (selectedCause.value !== '') parts.push(`「${causeDescription(selectedCause.value)}」`)
  return parts.join(' + ')
})

/** Header subtitle: the count, and the reviewed split when it is useful. */
const subtitle = computed<string>(() => {
  const total = entries.value.length
  if (total === 0) return ''
  const reviewed = entries.value.filter((e) => e.reviewed).length
  return `${total} 条错题 · 已复习 ${reviewed} 条`
})

/**
 * True when a filter is active and that is the only reason the list is
 * empty. Distinguishes "you have no mistakes" from "this filter has none",
 * which need different copy and different actions — the first offers the
 * practice page, the second offers to clear the filters.
 */
const filteredToNothing = computed<boolean>(
  () => entries.value.length > 0 && filteredEntries.value.length === 0
)

/* ---------------------------------------------------------------------
 * Layered feedback release.
 *
 * The gating itself now lives in `LayeredFeedback`, which owns its own
 * release state — one instance per entry, so this page keeps no
 * bookkeeping of its own and two entries can sit at different layers
 * without any of it being coordinated from here. See that component for
 * why the sections are released one at a time and why progress is not
 * persisted.
 *
 * What stays here is the one question the card has to answer before it
 * can render one at all: does this entry have anything to show?
 * ------------------------------------------------------------------- */

/**
 * Whether the analysis produced any prose.
 *
 * A `done` entry whose three sections are all empty falls through to the
 * same note as `pending`: the analysis ran but said nothing. Showing an
 * empty labelled panel would look like a rendering bug, and there is
 * genuinely nothing to offer.
 */
function hasFeedback(entry: MistakeEntry): boolean {
  const sections: FeedbackSection[] = ['error_detail', 'hint', 'suggested_fix']
  return sections.some((s) => entry[s] !== '')
}

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    entries.value = await listMistakes()
  } catch (err) {
    errorMsg.value = (err as Error).message
    entries.value = []
  } finally {
    loading.value = false
  }
}

async function markReviewed(entry: MistakeEntry): Promise<void> {
  if (pending.value.has(entry.id)) return
  pending.value = new Set(pending.value).add(entry.id)
  try {
    const updated = await updateMistake(entry.id, { reviewed: true })
    const idx = entries.value.findIndex((e) => e.id === entry.id)
    // Guard against the list having been reloaded while the request was
    // in flight: the index may now belong to a different entry.
    if (idx !== -1 && entries.value[idx].id === entry.id) {
      entries.value[idx] = updated
    }
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    const next = new Set(pending.value)
    next.delete(entry.id)
    pending.value = next
  }
}

/** How often to ask whether a retry has landed, and for how long. */
const ANALYSIS_POLL_INTERVAL_MS = 3000
const ANALYSIS_POLL_ATTEMPTS = 60

/**
 * Replace an entry in the list, guarding against a concurrent reload.
 *
 * The index is looked up fresh rather than captured: the list can be
 * reloaded while a request is in flight, and the old index would then
 * belong to a different entry.
 */
function replaceEntry(id: number, updated: MistakeEntry): void {
  const idx = entries.value.findIndex((e) => e.id === id)
  if (idx !== -1 && entries.value[idx].id === id) {
    entries.value[idx] = updated
  }
}

/**
 * Ask for another analysis and wait for it to land (P2-24).
 *
 * The sidecar answers as soon as the retry is accepted and does the work
 * in the background, so the outcome can only be learned by asking again.
 * Polling rather than the WebSocket because a retry is not a judge run —
 * there is no stream to subscribe to.
 */
async function reanalyse(entry: MistakeEntry): Promise<void> {
  if (reanalysing.value.has(entry.id)) return
  reanalysing.value = new Set(reanalysing.value).add(entry.id)
  try {
    replaceEntry(entry.id, await analyzeMistake(entry.id))
    for (let i = 0; i < ANALYSIS_POLL_ATTEMPTS; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, ANALYSIS_POLL_INTERVAL_MS))
      const latest = await getMistake(entry.id)
      replaceEntry(entry.id, latest)
      if (latest.analysis_status !== 'pending') return
    }
    errorMsg.value = '分析用时过长，请稍后刷新查看'
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    const next = new Set(reanalysing.value)
    next.delete(entry.id)
    reanalysing.value = next
  }
}

async function remove(entry: MistakeEntry): Promise<void> {
  if (pending.value.has(entry.id)) return
  pending.value = new Set(pending.value).add(entry.id)
  try {
    await deleteMistake(entry.id)
    entries.value = entries.value.filter((e) => e.id !== entry.id)
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    const next = new Set(pending.value)
    next.delete(entry.id)
    pending.value = next
  }
}

function selectTag(tag: string | null): void {
  selectedTag.value = tag
}

/**
 * Drop every filter at once.
 *
 * The empty state's action, and the reason it is not `selectTag(null)`
 * any more: with two filters, clearing only the tag would leave the user
 * staring at the same empty list they just asked to leave.
 */
function clearFilters(): void {
  selectedTag.value = null
  selectedCause.value = ''
}

/** Practise the problem this entry came from — the useful next action. */
function practise(problemId: number): void {
  void router.push({ name: 'problem', params: { id: problemId } })
}

function goToPractice(): void {
  void router.push({ name: 'practice' })
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  // `new Date` never throws on a bad string — it yields Invalid Date, and
  // `toLocaleString` then returns "Invalid Date". Checking `isNaN` is what
  // actually keeps a malformed timestamp from being printed raw.
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<template>
  <div class="mistakes-view">
    <div class="content">
      <PageHeader
        title="错题本"
        :subtitle="subtitle"
        :loading="loading"
        :error="errorMsg"
        @refresh="load"
      />

      <!--
        Filter bar. Rendered even with one entry so the reserved 错因筛选
        slot is always visible: a control that appears and disappears with
        the data cannot teach anyone that it exists.
      -->
      <div class="filter-bar">
        <nav v-if="allTags.length" class="tag-bar" aria-label="按知识点筛选">
          <button
            class="tag-chip"
            :class="{ active: selectedTag === null }"
            type="button"
            :aria-pressed="selectedTag === null"
            @click="selectTag(null)"
          >
            全部 ({{ entries.length }})
          </button>
          <button
            v-for="tag in allTags"
            :key="tag"
            class="tag-chip"
            :class="{ active: selectedTag === tag }"
            type="button"
            :aria-pressed="selectedTag === tag"
            @click="selectTag(tag)"
          >
            {{ tag }}
          </button>
        </nav>

        <!--
          P2-21's error-cause filter. Rendered only when at least one
          loaded entry carries a label — see the docstring: the old
          always-visible slot was teaching, and a working control with
          nothing to choose from would be the dead control that slot
          existed to avoid.
        -->
        <div v-if="allCauses.length" class="cause-filter">
          <label class="cause-label" for="cause-select">错因</label>
          <select
            id="cause-select"
            v-model="selectedCause"
            class="cause-select"
            title="按 AI 判定出的错因分类筛选"
          >
            <option value="">全部错因</option>
            <option v-for="cause in allCauses" :key="cause.label" :value="cause.label">
              {{ cause.description }}
            </option>
          </select>
        </div>
      </div>

      <!-- Loading. First paint only — a refresh keeps the list on screen. -->
      <div v-if="loading && entries.length === 0" class="placeholder">加载错题本中…</div>

      <!--
        Empty states. Both are skipped while an error is showing: the
        failure is the state, and "还没有错题" beside it would contradict it.
      -->
      <EmptyState
        v-else-if="!errorMsg && entries.length === 0"
        icon="book"
        title="错题本还是空的"
        explanation="只有判题未通过的提交才会被收进这里。去练习页挑一道题提交一次，答错的题目会自动归档，之后就能在复习计划里按记忆曲线回顾。"
        action-label="去练习题"
        @navigate="goToPractice"
      />

      <EmptyState
        v-else-if="!errorMsg && filteredToNothing"
        variant="search"
        icon="search"
        :title="`${activeFilterLabel}下还没有错题`"
        explanation="换个知识点或错因，或者回到全部看看。"
        action-label="查看全部错题"
        @navigate="clearFilters"
      />

      <!-- Entry list -->
      <div v-if="filteredEntries.length > 0" class="entry-list">
        <article
          v-for="entry in filteredEntries"
          :key="entry.id"
          class="entry-card"
          :class="{ reviewed: entry.reviewed, busy: pending.has(entry.id) }"
        >
          <div class="entry-header">
            <button
              class="entry-title"
              type="button"
              :title="`练习题目 #${entry.problem_id}`"
              @click="practise(entry.problem_id)"
            >
              题目 #{{ entry.problem_id }}
            </button>
            <span class="entry-date">{{ formatDate(entry.created_at) }}</span>
            <span
              class="review-badge"
              :class="entry.reviewed ? 'review-badge-done' : 'review-badge-todo'"
            >
              {{ entry.reviewed ? '已复习' : '未复习' }}
            </span>
          </div>

          <p v-if="entry.error_cause" class="entry-cause">{{ entry.error_cause }}</p>
          <p v-else class="entry-cause entry-cause-empty">尚未填写错因</p>

          <!--
            AI 诊断（P2-24），分层释放。

            The gating lives in `LayeredFeedback`; see that component for
            why the sections are released one at a time. Note that the
            initial state is *nothing shown* — even 错在哪 sits behind a
            press. That is the point of the gate: the first thing the
            student does is commit to having looked at their own code.
            Revealing 错在哪 by default would make the button a "show me
            the rest" control, which is a weaker prompt to think.

            A `done` entry with nothing to show falls through to the same
            note as `pending`: the analysis ran but produced no prose.
            Showing an empty labelled panel would look like a rendering
            bug, and there is genuinely nothing to offer.
          -->
          <div class="entry-analysis">
            <LayeredFeedback
              v-if="entry.analysis_status === 'done' && hasFeedback(entry)"
              :error-detail="entry.error_detail"
              :hint="entry.hint"
              :suggested-fix="entry.suggested_fix"
              :error-type-description="entry.error_type_description"
              :error-type="entry.agent_error_type"
            />

            <p
              v-else-if="entry.analysis_status === 'pending' || entry.analysis_status === 'done'"
              class="analysis-note"
            >
              {{ reanalysing.has(entry.id) ? '正在重新分析…' : 'AI 分析中…' }}
            </p>

            <div v-else class="analysis-failed">
              <p class="analysis-note analysis-note-failed">AI 分析失败</p>
              <button
                class="btn btn-analyse"
                type="button"
                :disabled="reanalysing.has(entry.id)"
                @click="reanalyse(entry)"
              >
                {{ reanalysing.has(entry.id) ? '分析中…' : '重新分析' }}
              </button>
            </div>
          </div>

          <div v-if="entry.knowledge_tags.length" class="entry-tags">
            <span v-for="tag in entry.knowledge_tags" :key="tag" class="entry-tag">
              {{ tag }}
            </span>
          </div>

          <div class="entry-meta">
            <span class="meta-item">提交 #{{ entry.submission_id }}</span>
            <span class="meta-item">复习 {{ entry.review_count }} 次</span>
            <span v-if="entry.last_reviewed_at" class="meta-item">
              上次复习 {{ formatDate(entry.last_reviewed_at) }}
            </span>
          </div>

          <div class="entry-actions">
            <button
              v-if="!entry.reviewed"
              class="btn btn-review"
              type="button"
              :disabled="pending.has(entry.id)"
              @click="markReviewed(entry)"
            >
              {{ pending.has(entry.id) ? '处理中…' : '标记已复习' }}
            </button>
            <button
              class="btn btn-delete"
              type="button"
              :disabled="pending.has(entry.id)"
              @click="remove(entry)"
            >
              删除
            </button>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
/*
 * No hex literals except the one documented in `.entry-cause-empty`. Every
 * colour resolves through a PU-0 token; the reviewed/not-reviewed pair is
 * the only place this page needs a semantic colour and it uses the same
 * success/danger tokens the verdict badges do.
 */
.mistakes-view {
  /* PU-6: was 100vh. The app shell owns the viewport now and stacks a
   * topbar above this view, so the page fills its container instead. */
  height: 100%;
  overflow-y: auto;
  background: var(--af-canvas);
  color: var(--af-text-primary);
}

/* The centred content column — see `--af-content-max` in tokens.css. The
 * scroll container is the outer element so the scrollbar sits at the
 * viewport edge rather than inside the column's padding. */
.content {
  max-width: var(--af-content-max);
  margin: 0 auto;
  padding: 20px 16px 32px 16px;
  box-sizing: border-box;
}

/* --- filter bar --- */
.filter-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--af-border);
}

.tag-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.tag-chip {
  padding: 3px 12px;
  font-family: inherit;
  font-size: 12px;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: 999px;
  cursor: pointer;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    color var(--af-duration-fast) var(--af-ease),
    border-color var(--af-duration-fast) var(--af-ease);
}

.tag-chip:hover {
  color: var(--af-text-primary);
  background: var(--af-overlay);
}

.tag-chip.active {
  color: var(--af-accent);
  background: var(--af-accent-subtle);
  border-color: var(--af-accent);
}

/* --- error-cause filter (P2-21) --- */
.cause-filter {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.cause-label {
  font-size: 12px;
  color: var(--af-text-muted);
}

/*
 * A native <select>: it is keyboard- and screen-reader-correct for free,
 * which matters more here than matching the chip bar's look — the chips
 * are a handful of short tags, while the causes are up to a dozen long
 * Chinese phrases that would not fit in a row.
 *
 * Solid border rather than the dashed one the disabled button carried:
 * dashed meant "not yet usable" (the label beside it said why). This one
 * is usable, and leaving it dashed would keep saying otherwise.
 */
.cause-select {
  padding: 3px 8px;
  font-family: inherit;
  font-size: 12px;
  color: var(--af-text-secondary);
  background: var(--af-surface);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.cause-select:hover {
  border-color: var(--af-accent);
}

.cause-select:focus-visible {
  outline: 2px solid var(--af-accent);
  outline-offset: 1px;
}

.placeholder {
  padding: 40px 16px;
  font-size: 14px;
  text-align: center;
  color: var(--af-text-muted);
}

/* --- entry list --- */
.entry-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.entry-card {
  /* An inset accent bar rather than a border-left: `border-left` shifts
   * the content box by its width, so the reviewed and unreviewed cards
   * would have text at different x positions whenever the widths differ. */
  position: relative;
  padding: 14px 16px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  transition: opacity var(--af-duration-base) var(--af-ease);
}

.entry-card::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 3px;
  background: var(--af-danger);
  border-radius: var(--af-radius-md) 0 0 var(--af-radius-md);
}

.entry-card.reviewed::before {
  background: var(--af-success);
}

/* A reviewed entry is a closed loop, so it recedes; the accent bar keeps
 * enough of it legible that it can still be found by scrolling. */
.entry-card.reviewed {
  opacity: 0.72;
}

.entry-card.busy {
  opacity: 0.55;
}

.entry-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 8px;
}

.entry-title {
  padding: 0;
  font-family: inherit;
  font-size: 15px;
  font-weight: 600;
  color: var(--af-text-primary);
  background: none;
  border: none;
  cursor: pointer;
}

.entry-title:hover {
  color: var(--af-accent);
  text-decoration: underline;
}

.entry-date {
  font-size: 12px;
  color: var(--af-text-muted);
}

.review-badge {
  padding: 1px 8px;
  font-size: 11px;
  border-radius: 999px;
  white-space: nowrap;
}

.review-badge-done {
  color: var(--af-success);
  background: color-mix(in srgb, var(--af-success) 18%, transparent);
}

.review-badge-todo {
  color: var(--af-warning);
  background: color-mix(in srgb, var(--af-warning) 18%, transparent);
}

.entry-cause {
  margin: 8px 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--af-text-primary);
  /* Error causes are free text and can be a pasted compiler message. */
  white-space: pre-wrap;
  word-break: break-word;
}

.entry-cause-empty {
  font-style: italic;
  color: var(--af-text-muted);
}

/*
 * The analysis block itself now lives in `LayeredFeedback.vue` — the same
 * component `Practice.vue` renders after a verdict, so the two surfaces
 * cannot drift apart. What stays here is only the chrome around it: the
 * spacing above it, the note shown while an analysis is pending, and the
 * failed-analysis row.
 */
.entry-analysis {
  margin-top: 10px;
}

.analysis-note {
  margin: 0;
  font-size: 12px;
  color: var(--af-text-muted);
}

.analysis-note-failed {
  color: var(--af-danger);
}

.analysis-failed {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.btn-analyse {
  color: var(--af-accent);
  background: var(--af-accent-subtle);
  border-color: var(--af-accent);
}

.entry-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0;
}

.entry-tag {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-accent);
  background: var(--af-accent-subtle);
  border-radius: 999px;
}

.entry-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--af-text-muted);
}

.entry-actions {
  display: flex;
  gap: 8px;
}

.btn {
  padding: 4px 12px;
  font-family: inherit;
  font-size: 12px;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    opacity var(--af-duration-fast) var(--af-ease);
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/*
 * 标记已复习 is the primary action on the card, so it is the filled one;
 * 删除 is destructive and irreversible, so it is a muted outline that
 * only goes red on hover. Both were filled red/green before, which made
 * deleting look as safe as reviewing.
 */
.btn-review {
  color: #ffffff;
  background: var(--af-accent);
}

.btn-review:hover:not(:disabled) {
  background: var(--af-accent-hover);
}

.btn-delete {
  color: var(--af-text-secondary);
  background: transparent;
  border-color: var(--af-border-strong);
}

.btn-delete:hover:not(:disabled) {
  color: var(--af-danger);
  background: color-mix(in srgb, var(--af-danger) 14%, transparent);
  border-color: var(--af-danger);
}

/* Narrow: the filter bar stacks, and the cause slot moves under the tags
 * rather than being squeezed off the right edge. */
@media (max-width: 720px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .cause-filter {
    justify-content: flex-start;
  }
}
</style>
