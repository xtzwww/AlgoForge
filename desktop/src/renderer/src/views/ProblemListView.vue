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
import { NButton, NInput, NSelect } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
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

/** 下拉选项：难度与状态的 facets（NSelect 的 SelectOption 形状）。 */
const difficultyOptions: SelectOption[] = DIFFICULTIES.map((d) => ({
  label: d.label,
  value: d.value
}))
const statusOptions: SelectOption[] = STATUSES.map((s) => ({
  label: s.label,
  value: s.value
}))

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
        <NButton size="small" :loading="loading" @click="load">刷新</NButton>
      </div>
    </header>

    <!-- Homework (阶段 4). Open endpoint: no login needed to see it. -->
    <nav v-if="assignments.length > 0" class="assignment-bar">
      <span class="assignment-bar-label">作业</span>
      <NButton
        v-for="a in assignments"
        :key="a.id"
        size="small"
        round
        :type="activeAssignmentId === a.id ? 'primary' : a.overdue ? 'error' : 'default'"
        @click="toggleAssignment(a)"
      >
        {{ a.title }}
        <span v-if="a.due_at" class="assignment-due">{{ dueText(a.due_at) }}</span>
      </NButton>
    </nav>

    <!-- Filter bar -->
    <div class="filter-bar">
      <div class="filter-row">
        <label class="filter-group">
          <span class="filter-label">难度</span>
          <NSelect
            v-model:value="difficultyFilter"
            class="filter-select"
            :options="difficultyOptions"
            size="small"
          />
        </label>

        <label class="filter-group">
          <span class="filter-label">状态</span>
          <NSelect
            v-model:value="statusFilter"
            class="filter-select"
            :options="statusOptions"
            size="small"
          />
        </label>

        <label class="filter-group filter-group-search">
          <span class="filter-label">搜索</span>
          <NInput
            v-model:value="searchQuery"
            type="text"
            class="filter-input"
            placeholder="按题目标题搜索"
            aria-label="按题目标题搜索"
          />
        </label>

        <NButton
          v-if="
            difficultyFilter !== 'all' ||
            statusFilter !== 'all' ||
            selectedTag !== null ||
            activeAssignmentId !== null ||
            searchQuery.trim() !== ''
          "
          size="tiny"
          quaternary
          @click="resetFilters"
        >
          清除筛选
        </NButton>
      </div>

      <nav v-if="allTags.length" class="tag-bar">
        <NButton
          size="small"
          round
          :type="selectedTag === null ? 'primary' : 'default'"
          @click="selectedTag = null"
        >
          全部知识点
        </NButton>
        <NButton
          v-for="tag in allTags"
          :key="tag"
          size="small"
          round
          :type="selectedTag === tag ? 'primary' : 'default'"
          @click="selectedTag = tag"
        >
          {{ tag }}
        </NButton>
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
    <div v-if="filteredProblems.length > 0" class="problem-list af-stagger">
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
          <NButton size="small" type="primary" @click="practice(p.id)">开始练习</NButton>
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

.assignment-due {
  font-size: 11px;
  color: var(--af-text-muted);
}

.problem-list-view {
  display: flex;
  flex-direction: column;
  /* PU-6: was 100vh. The app shell owns the viewport now and stacks a title
   * bar above this view, so the page must fill its container instead. */
  height: 100%;
  padding: 16px;
  box-sizing: border-box;
  color: var(--af-text-primary);
  /* No `background` (2026-09-25). This used to paint `#1a1a2e` over the
   * entire page, which on the new palette would have hidden the canvas
   * glow and grid completely — this would have been the one screen that
   * still looked like the old design, and it is the screen a student
   * opens most. The canvas belongs to `body`; a page's job is to not
   * cover it. */
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
  font-weight: 600;
  letter-spacing: -0.01em;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.solved-summary {
  font-size: 13px;
  color: var(--af-text-secondary);
}

.solved-summary strong {
  color: var(--af-success);
  font-size: 16px;
  /* The number people actually read on this page, so it gets a halo.
   * `tabular-nums` is already inherited from `body`, which is what keeps
   * it from jittering as the count changes. */
  text-shadow: 0 0 14px color-mix(in srgb, var(--af-success) 45%, transparent);
}

.filter-bar {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--af-border);
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
  color: var(--af-text-secondary);
}

.filter-select {
  padding: 6px 10px;
  font-size: 13px;
  font-family: inherit;
  color: var(--af-text-primary);
  /* Inputs keep the OPAQUE `--af-surface`, not the glass treatment. A
   * translucent field over the canvas grid is harder to read and gains
   * nothing: there is no content behind an input worth seeing through
   * it. Glass is for panels that *contain* things. */
  background: var(--af-surface);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    border-color var(--af-duration-fast) var(--af-ease),
    box-shadow var(--af-duration-fast) var(--af-ease);
}

/* The search field is the only free-text control in this bar, so it
   gets to be wider than the selects — a title fragment is longer than
   a dropdown label. `flex: 1` lets it take the leftover row width. */
.filter-group-search {
  flex: 1 1 220px;
  min-width: 180px;
}

/* 布局：搜索框吃满剩余宽度。控件外观由 NInput/NSelect 负责。 */
.filter-input {
  flex: 1;
  min-width: 0;
}

.tag-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* Message blocks.
 *
 * The old values were opaque dark red and brown (`#7f1d1d`, `#78350f`)
 * tuned for the old flat navy. On a translucent panel they read as
 * opaque stickers glued on top, so these are now a tint of the semantic
 * colour plus a matching hairline: the meaning survives, the panel shows
 * through, and the full-strength colour stays on the *text*, which is
 * where contrast actually matters.
 *
 * `font-size` is now set on both. `.warn-msg` had 13px and `.error-msg`
 * had none, so the two messages that occupy the same slot in the
 * template rendered at different sizes. */
.error-msg {
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--af-danger);
  background: color-mix(in srgb, var(--af-danger) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-danger) 35%, transparent);
  border-radius: var(--af-radius-sm);
}

.warn-msg {
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--af-warning);
  background: color-mix(in srgb, var(--af-warning) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-warning) 35%, transparent);
  border-radius: var(--af-radius-sm);
}

.empty {
  padding: 40px 16px;
  font-size: 14px;
  color: var(--af-text-muted);
  text-align: center;
}

.empty code {
  padding: 2px 6px;
  color: var(--af-accent-hover);
  background: var(--af-elevated);
  border-radius: var(--af-radius-sm);
}

.problem-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* Glass plus a hover lift (2026-09-25). These are the rows a student
 * scans and clicks all session, so they get the two treatments that say
 * "this is a thing you can open": the panel lets the canvas through,
 * and it rises 2px under the cursor.
 *
 * The left border is NOT repainted on hover, unlike the generic
 * `.af-lift` in motion.css — here it carries the difficulty/availability
 * marker, so recolouring it would make the row look like it changed
 * state rather than like it is under the cursor. */
.problem-card {
  padding: 16px;
  background: var(--af-surface);
  border-radius: var(--af-radius-md);
  border-left: 4px solid var(--af-accent);
  transition:
    transform var(--af-duration-base) var(--af-ease),
    box-shadow var(--af-duration-base) var(--af-ease),
    background var(--af-duration-base) var(--af-ease);
}

@supports (backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)) {
  .problem-card {
    background: color-mix(in srgb, var(--af-surface) 78%, transparent);
    -webkit-backdrop-filter: blur(14px) saturate(140%);
    backdrop-filter: blur(14px) saturate(140%);
  }
}

.problem-card:hover {
  transform: translateY(-2px);
  background: var(--af-elevated);
  box-shadow: var(--af-glow-accent-soft);
}

.problem-card.inactive {
  opacity: 0.55;
  border-left-color: var(--af-text-muted);
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
  font-family: var(--af-font-mono);
  color: var(--af-text-muted);
}

.problem-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

/* Badges.
 *
 * Each is a 16% tint of its semantic colour for the fill, with the
 * full-strength colour as the text. The old design did the opposite:
 * opaque dark blocks (`#14532d`, `#78350f`, `#7f1d1d`) with a pale tint
 * as the text. That only works on a flat opaque background — on a glass
 * card those solid blocks punch holes in the panel. Swapping which end
 * is the fill also *raises* the text contrast instead of lowering it. */
.badge {
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 999px;
  white-space: nowrap;
}

.badge-easy,
.badge-solved {
  color: var(--af-success);
  background: color-mix(in srgb, var(--af-success) 16%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-success) 32%, transparent);
}

.badge-medium {
  color: var(--af-warning);
  background: color-mix(in srgb, var(--af-warning) 16%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-warning) 32%, transparent);
}

.badge-hard {
  color: var(--af-danger);
  background: color-mix(in srgb, var(--af-danger) 16%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-danger) 32%, transparent);
}

.badge-unsolved,
.badge-inactive {
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
}

.card-tags {
  margin: 0 0 8px 0;
  font-size: 12px;
  color: var(--af-accent-hover);
}

.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: var(--af-text-secondary);
  margin-bottom: 12px;
}

.card-actions {
  display: flex;
  justify-content: flex-end;
}

@media (prefers-reduced-motion: reduce) {
  .problem-card:hover {
    transform: none;
  }
}
</style>
