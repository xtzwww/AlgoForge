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
 * Navigation is a route push (PU-7): selecting a problem goes to
 * `/problems/:id`, which is what puts the choice in the URL. It stays a
 * `<button>` rather than becoming a `<RouterLink>` because this view is
 * not part of the C-group visual rewrite — turning it into an anchor
 * would mean restyling `.practice-btn` here, which is PU-9's territory.
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listProblems, type ProblemSummary } from '../api/problems'
import { fetchVerdictsByProblem, type ProblemVerdict } from '../api/submissions'
import { getOpenAssignments, type AssignmentOut } from '../api/assignments'

const router = useRouter()

/**
 * Practise a problem: the id becomes a route param, and the route's
 * `props` function hands it to the practice page as a number. Free-run
 * mode is the absence of that param, so there is nothing to clear here.
 */
function practice(problemId: number): void {
  void router.push({ name: 'problem', params: { id: problemId } })
}

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

/**
 * Title search text.
 *
 * Filtered client-side, matching the rest of this bar, because the
 * whole catalogue is already in memory (one page at the 100 ceiling).
 * The server *does* accept `q` — see `ListProblemsParams.q` — but using
 * it here would mean a request per keystroke to narrow a list we
 * already have, and typing "排序" would empty the view for the length
 * of a round trip.
 *
 * Lowercased on both sides so the match is case-insensitive; the server
 * has no opinion about which collation SQLite happens to be using.
 */
const searchQuery = ref('')

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
    // Homework first: it is the narrowest filter and the one that was
    // asked for, so it wins over the browse-filters rather than being
    // an extra chip competing with them.
    if (activeAssignment.value !== null && !activeAssignment.value.problem_ids.includes(p.id)) {
      return false
    }
    if (searchQuery.value.trim() !== '') {
      const needle = searchQuery.value.trim().toLowerCase()
      if (!p.title.toLowerCase().includes(needle)) return false
    }
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
 * Homework is read from an open endpoint (阶段 4).
 *
 * Nothing here requires a login: a student has to be able to see what
 * was set, and the desktop client's normal path is not logged in. A
 * failure to load it is also deliberately quiet — homework is an
 * addition to a browseable catalogue, not the thing that makes the page
 * work, and a failed fetch must not take the problem list down with it.
 */
const assignments = ref<AssignmentOut[]>([])
const activeAssignmentId = ref<number | null>(null)

const activeAssignment = computed<AssignmentOut | null>(
  () => assignments.value.find((a) => a.id === activeAssignmentId.value) ?? null
)

function toggleAssignment(assignment: AssignmentOut): void {
  activeAssignmentId.value = activeAssignmentId.value === assignment.id ? null : assignment.id
}

async function loadAssignments(): Promise<void> {
  try {
    assignments.value = await getOpenAssignments()
  } catch {
    // See the comment above: the catalogue is the page; homework is not.
    assignments.value = []
  }
}

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

  await loadAssignments()

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
  activeAssignmentId.value = null
  searchQuery.value = ''
}

function dueText(dueAt: string | null): string {
  if (!dueAt) return ''
  // ISO from the server; only the date part is worth showing a student.
  return dueAt.slice(0, 10)
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

    <!-- Homework (阶段 4). Open endpoint: no login needed to see it. -->
    <nav v-if="assignments.length > 0" class="assignment-bar">
      <span class="assignment-bar-label">作业</span>
      <button
        v-for="a in assignments"
        :key="a.id"
        class="assignment-chip"
        :class="{ active: activeAssignmentId === a.id, overdue: a.overdue }"
        @click="toggleAssignment(a)"
      >
        {{ a.title }}
        <span v-if="a.due_at" class="assignment-due">{{ dueText(a.due_at) }}</span>
      </button>
    </nav>

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

        <label class="filter-group filter-group-search">
          <span class="filter-label">搜索</span>
          <input
            v-model="searchQuery"
            type="search"
            class="filter-input"
            placeholder="按题目标题搜索"
            aria-label="按题目标题搜索"
          />
        </label>

        <button
          v-if="
            difficultyFilter !== 'all' ||
            statusFilter !== 'all' ||
            selectedTag !== null ||
            activeAssignmentId !== null ||
            searchQuery.trim() !== ''
          "
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
      <template v-if="searchQuery.trim() !== ''">
        没有标题包含「{{ searchQuery.trim() }}」的题目。
      </template>
      <template v-else>没有符合当前筛选条件的题目。</template>
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
          <button class="practice-btn" @click="practice(p.id)">开始练习</button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
/* --- Homework (阶段 4) --- */

.assignment-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.assignment-bar-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-muted);
}

.assignment-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 10px;
  font-size: 12px;
  font-family: inherit;
  color: var(--af-text-secondary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border-strong);
  border-radius: 999px;
  cursor: pointer;
}

.assignment-chip:hover {
  border-color: var(--af-accent);
}

.assignment-chip.active {
  color: #ffffff;
  background: var(--af-accent);
  border-color: var(--af-accent);
}

.assignment-chip.overdue:not(.active) {
  color: var(--af-danger);
  border-color: var(--af-danger);
}

.assignment-due {
  font-size: 11px;
  color: var(--af-text-muted);
}

.assignment-chip.active .assignment-due {
  color: #ffffff;
}

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

/* The search field is the only free-text control in this bar, so it
   gets to be wider than the selects — a title fragment is longer than
   a dropdown label. `flex: 1` lets it take the leftover row width. */
.filter-group-search {
  flex: 1 1 220px;
  min-width: 180px;
}

.filter-input {
  flex: 1;
  padding: 6px 10px;
  font-size: 13px;
  color: #e5e7eb;
  background: #16213e;
  border: 1px solid #374151;
  border-radius: 4px;
}

.filter-input::placeholder {
  color: #6b7280;
}

.filter-input:focus {
  outline: none;
  border-color: #3b82f6;
}

.filter-input::-webkit-search-cancel-button {
  cursor: pointer;
}

/* The search field is the only free-text control in this bar, so it
   gets to be wider than the selects — a title fragment is longer than
   a dropdown label. */
.filter-group-search {
  flex: 1 1 220px;
  min-width: 180px;
}

.filter-input {
  flex: 1;
  padding: 6px 10px;
  font-size: 13px;
  color: #e5e7eb;
  background: #16213e;
  border: 1px solid #374151;
  border-radius: 4px;
}

.filter-input::placeholder {
  color: #6b7280;
}

.filter-input:focus {
  outline: none;
  border-color: #3b82f6;
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
