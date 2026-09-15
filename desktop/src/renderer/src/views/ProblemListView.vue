<script setup lang="ts">
/**
 * ProblemListView — the problem catalogue (PU-14).
 *
 * Layout:
 *   ┌── Header: 标题 + 刷新 ───────────────────────────────┐
 *   ├── Filter bar ────────────────────────────────────────┤
 *   │  [全部] [入门] [中等] [困难]  ·  [全部状态] [未通过] [已通过] │
 *   │  knowledge-tag chips (union of tags in view)          │
 *   ├───────────────────────────────────────────────────────┤
 *   │  Problem card:                                        │
 *   │    #id · title · difficulty badge · 已通过 / 未通过      │
 *   │    knowledge-tag chips                                │
 *   │    limits + source                                    │
 *   │    [开始练习]                                          │
 *   └───────────────────────────────────────────────────────┘
 *
 * Where the data comes from:
 *
 * - The catalogue rows come from `GET /api/v1/problems` (PU-12),
 *   paged. 20 problems fit in one page at the server's 100 ceiling,
 *   so this view loads a single page and filters client-side rather
 *   than re-querying the server on every chip click. The server *can*
 *   filter by tag and difficulty; reaching for that only starts to pay
 *   off once the catalogue outgrows a page.
 * - The 已通过 column is not in the catalogue. It is folded from the
 *   newest page of submission history client-side — see
 *   `api/submissions.ts` for why, and for the window's limits.
 * - A verdict fetch that fails must not take the catalogue down with
 *   it: the problems are still worth listing without the badge, so the
 *   failure is reported alongside them instead of replacing them.
 *
 * Navigation is an event, not a route: this app has no vue-router (see
 * App.vue). Selecting a problem emits `select`, and the shell switches
 * to the practice tab with that id.
 */
import { computed, onMounted, ref } from 'vue'
import { listProblems, type ProblemSummary } from '../api/problems'
import { fetchVerdictsByProblem, type ProblemVerdict } from '../api/submissions'

const emit = defineEmits<{ (e: 'select', problemId: number): void }>()

const problems = ref<ProblemSummary[]>([])
const verdicts = ref<Map<number, ProblemVerdict>>(new Map())
const loading = ref(false)
const errorMsg = ref('')
/** Non-fatal: the catalogue rendered, but the verdict column did not. */
const verdictError = ref('')

type DifficultyFilter = 'all' | 'easy' | 'medium' | 'hard'
/** `all` / `unsolved` / `solved` — the tri-state pass filter. */
type StatusFilter = 'all' | 'unsolved' | 'solved'

const difficultyFilter = ref<DifficultyFilter>('all')
const statusFilter = ref<StatusFilter>('all')
const selectedTag = ref<string | null>(null)

/** Difficulty in the facet's display order, not alphabetical. */
const DIFFICULTIES: { value: DifficultyFilter; label: string }[] = [
  { value: 'all', label: '全部难度' },
  { value: 'easy', label: '入门' },
  { value: 'medium', label: '中等' },
  { value: 'hard', label: '困难' }
]

const DIFFICULTY_LABELS: Record<string, string> = {
  easy: '入门',
  medium: '中等',
  hard: '困难'
}

const STATUSES: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '全部状态' },
  { value: 'unsolved', label: '未通过' },
  { value: 'solved', label: '已通过' }
]

/** Tags across the whole catalogue, sorted. */
const allTags = computed<string[]>(() => {
  const set = new Set<string>()
  for (const p of problems.value) {
    for (const t of p.knowledge_tags) set.add(t)
  }
  return Array.from(set).sort()
})

/**
 * The rows after all three filters.
 *
 * Tag filtering is applied here rather than against `allTags` so a tag
 * only appears in the bar when the *catalogue* has it — the chips are
 * built from `allTags`, and a chip that can only ever yield an empty
 * list is worse than no chip.
 */
const filteredProblems = computed<ProblemSummary[]>(() =>
  problems.value.filter((p) => {
    if (difficultyFilter.value !== 'all' && p.difficulty !== difficultyFilter.value) {
      return false
    }
    if (selectedTag.value !== null && !p.knowledge_tags.includes(selectedTag.value)) {
      return false
    }
    if (statusFilter.value !== 'all') {
      const passed = verdicts.value.get(p.id)?.passed ?? false
      if (statusFilter.value === 'solved' && !passed) return false
      if (statusFilter.value === 'unsolved' && passed) return false
    }
    return true
  })
)

/** How many of the loaded problems have been solved. */
const solvedCount = computed<number>(
  () => problems.value.filter((p) => verdicts.value.get(p.id)?.passed).length
)

/**
 * Load the catalogue and, independently, the verdict column.
 *
 * The two are deliberately not `Promise.all`: a failed verdict fetch
 * should leave a usable list of problems on screen, and `Promise.all`
 * would reject the whole thing over the less important half.
 */
async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  verdictError.value = ''

  try {
    const page = await listProblems({ page: 1, page_size: 100 })
    problems.value = page.items
  } catch (err) {
    errorMsg.value = (err as Error).message
    problems.value = []
    loading.value = false
    return
  }

  try {
    verdicts.value = await fetchVerdictsByProblem()
  } catch (err) {
    verdictError.value = (err as Error).message
  }

  loading.value = false
}

function verdictOf(id: number): ProblemVerdict | undefined {
  return verdicts.value.get(id)
}

function difficultyLabel(difficulty: string | null): string {
  if (!difficulty) return ''
  return DIFFICULTY_LABELS[difficulty] ?? difficulty
}

function formatTags(tags: string[]): string {
  return tags.join(' · ')
}

function resetFilters(): void {
  difficultyFilter.value = 'all'
  statusFilter.value = 'all'
  selectedTag.value = null
}

onMounted(load)
</script>

<template>
  <div class="problem-list-view">
    <header class="header">
      <h1>题库</h1>
      <div class="header-right">
        <span class="solved-summary">
          已通过 <strong>{{ solvedCount }}</strong> / {{ problems.length }}
        </span>
        <button class="refresh-btn" :disabled="loading" @click="load">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- Filter bar -->
    <div class="filter-bar">
      <div class="filter-row">
        <label class="filter-group">
          <span class="filter-label">难度</span>
          <select v-model="difficultyFilter" class="filter-select">
            <option v-for="d in DIFFICULTIES" :key="d.value" :value="d.value">
              {{ d.label }}
            </option>
          </select>
        </label>

        <label class="filter-group">
          <span class="filter-label">状态</span>
          <select v-model="statusFilter" class="filter-select">
            <option v-for="s in STATUSES" :key="s.value" :value="s.value">
              {{ s.label }}
            </option>
          </select>
        </label>

        <button
          v-if="difficultyFilter !== 'all' || statusFilter !== 'all' || selectedTag !== null"
          class="reset-btn"
          @click="resetFilters"
        >
          清除筛选
        </button>
      </div>

      <nav v-if="allTags.length" class="tag-bar">
        <button
          class="tag-chip"
          :class="{ active: selectedTag === null }"
          @click="selectedTag = null"
        >
          全部知识点
        </button>
        <button
          v-for="tag in allTags"
          :key="tag"
          class="tag-chip"
          :class="{ active: selectedTag === tag }"
          @click="selectedTag = tag"
        >
          {{ tag }}
        </button>
      </nav>
    </div>

    <!-- Error messages -->
    <div v-if="errorMsg" class="error-msg">无法加载题库: {{ errorMsg }}</div>
    <div v-else-if="verdictError" class="warn-msg">
      无法加载通过状态，题目列表仍可浏览: {{ verdictError }}
    </div>

    <!-- Empty states -->
    <div v-if="loading" class="empty">加载题库中...</div>
    <div v-else-if="!errorMsg && problems.length === 0" class="empty">
      题库为空。请先运行 <code>python tools/load_problems.py</code> 导入题目。
    </div>
    <div v-else-if="!errorMsg && filteredProblems.length === 0" class="empty">
      没有符合当前筛选条件的题目。
    </div>

    <!-- Problem cards -->
    <div v-if="filteredProblems.length > 0" class="problem-list">
      <article
        v-for="p in filteredProblems"
        :key="p.id"
        class="problem-card"
        :class="{ inactive: !p.is_active }"
      >
        <div class="card-header">
          <span class="problem-id">#{{ p.id }}</span>
          <h2 class="problem-title">{{ p.title }}</h2>
          <span v-if="p.difficulty" class="badge" :class="`badge-${p.difficulty}`">
            {{ difficultyLabel(p.difficulty) }}
          </span>
          <span v-if="!p.is_active" class="badge badge-inactive">已停用</span>
          <span class="badge" :class="verdictOf(p.id)?.passed ? 'badge-solved' : 'badge-unsolved'">
            {{ verdictOf(p.id)?.passed ? '已通过' : '未通过' }}
          </span>
        </div>

        <p v-if="p.knowledge_tags.length" class="card-tags">
          {{ formatTags(p.knowledge_tags) }}
        </p>

        <div class="card-meta">
          <span class="meta-item">时限 {{ p.time_limit }}s</span>
          <span class="meta-item">内存 {{ p.memory_limit }} MB</span>
          <span v-if="p.source" class="meta-item">{{ p.source }}</span>
          <span v-if="verdictOf(p.id)" class="meta-item">
            提交 {{ verdictOf(p.id)?.attempts }} 次
          </span>
        </div>

        <div class="card-actions">
          <button class="practice-btn" @click="emit('select', p.id)">开始练习</button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.problem-list-view {
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
  margin-bottom: 16px;
}

.header h1 {
  margin: 0;
  font-size: 22px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.solved-summary {
  font-size: 13px;
  color: #9ca3af;
}

.solved-summary strong {
  color: #22c55e;
  font-size: 16px;
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

.filter-bar {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #374151;
}

.filter-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 12px;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filter-label {
  font-size: 13px;
  color: #9ca3af;
}

.filter-select {
  padding: 6px 10px;
  font-size: 13px;
  color: #e5e7eb;
  background: #16213e;
  border: 1px solid #374151;
  border-radius: 4px;
  cursor: pointer;
}

.reset-btn {
  padding: 5px 12px;
  font-size: 12px;
  color: #d1d5db;
  background: transparent;
  border: 1px solid #4b5563;
  border-radius: 4px;
  cursor: pointer;
}

.reset-btn:hover {
  background: #374151;
}

.tag-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag-chip {
  padding: 4px 12px;
  font-size: 13px;
  color: #d1d5db;
  background: #374151;
  border: none;
  border-radius: 16px;
  cursor: pointer;
  transition: background 0.2s;
}

.tag-chip:hover {
  background: #4b5563;
}

.tag-chip.active {
  background: #3b82f6;
  color: white;
}

.error-msg {
  color: #ef4444;
  padding: 8px 12px;
  background: #7f1d1d;
  border-radius: 4px;
  margin-bottom: 12px;
}

.warn-msg {
  color: #fbbf24;
  padding: 8px 12px;
  background: #78350f;
  border-radius: 4px;
  margin-bottom: 12px;
  font-size: 13px;
}

.empty {
  color: #6b7280;
  text-align: center;
  padding: 40px 16px;
  font-size: 14px;
}

.empty code {
  padding: 2px 6px;
  background: #16213e;
  border-radius: 4px;
  color: #93c5fd;
}

.problem-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.problem-card {
  padding: 16px;
  background: #16213e;
  border-radius: 8px;
  border-left: 4px solid #3b82f6;
}

.problem-card.inactive {
  opacity: 0.6;
  border-left-color: #6b7280;
}

.card-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 8px;
}

.problem-id {
  font-size: 13px;
  color: #6b7280;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.problem-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.badge {
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 10px;
  white-space: nowrap;
}

.badge-easy {
  background: #14532d;
  color: #86efac;
}

.badge-medium {
  background: #78350f;
  color: #fbbf24;
}

.badge-hard {
  background: #7f1d1d;
  color: #fca5a5;
}

.badge-solved {
  background: #14532d;
  color: #86efac;
}

.badge-unsolved {
  background: #374151;
  color: #9ca3af;
}

.badge-inactive {
  background: #374151;
  color: #d1d5db;
}

.card-tags {
  margin: 0 0 8px 0;
  font-size: 12px;
  color: #93c5fd;
}

.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 12px;
}

.card-actions {
  display: flex;
  justify-content: flex-end;
}

.practice-btn {
  padding: 6px 20px;
  font-size: 13px;
  font-weight: 600;
  color: white;
  background: #3b82f6;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.2s;
}

.practice-btn:hover {
  background: #2563eb;
}
</style>
