<script setup lang="ts">
/**
 * TeacherProblemsView —— 教学运营：题库管理（2026-09-27）。
 *
 * 教师端此前只有作业里的"选题器"能看题库，没有增删改的落点；写接口
 * （POST /problems、POST /duplicate，2026-09-22 起带教师守卫）在后端
 * 齐全，前端一直没页面调用。这个面板补上：
 *
 *   - 列表：搜索（标题子串）/ 知识点 / 难度筛选 + 含停用开关 + 分页；
 *   - 新建 / 编辑：同一张表单，含**全部用例**（隐藏用例只有这里能编辑，
 *     列表与练习页永远只看到可见用例）；
 *   - 复制：连隐藏用例一起复制（后端语义，见 problems.py）；
 *   - 停用 / 启用：走 PATCH /active，只翻一个字段，不碰题面与用例。
 *
 * 筛选全部走服务端参数（q / knowledge_tag / difficulty / include_inactive），
 * 不在客户端过滤——题库会持续长大，全量拉下来迟早变成一次很慢的请求。
 * 知识点下拉的选项来自"见过的标签"累积（与作业选题器同一策略）：服务端
 * 没有列出全部标签的接口，而从语料看标签有限且高频复用。
 *
 * 用例编辑的隐藏/可见语义：`is_sample` 勾上 = 学生可见（题面里能看到），
 * 不勾 = 只有判题用。改完保存走 PUT 整体替换——请求顺序就是新的 ordinal
 * 顺序，所以这里不做重排，只做增删。
 */

import { computed, ref } from 'vue'
import {
  NButton,
  NCheckbox,
  NDynamicTags,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NTag,
  useDialog
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import TeacherGate from '../../components/TeacherGate.vue'
import PageHeader from '../../components/PageHeader.vue'
import { isTeacher, logout } from '../../api/session'
import {
  createProblem,
  duplicateProblem,
  getProblem,
  getProblemTestCases,
  listProblems,
  setProblemActive,
  updateProblem,
  type ProblemSummary
} from '../../api/problems'
import { messageOf } from './format'
import { useTeacherPanel } from './useTeacherPanel'

const loading = ref(false)
const errorMsg = ref('')
const dialog = useDialog()

// --- 列表 ---------------------------------------------------------------

const problems = ref<ProblemSummary[]>([])
const problemTotal = ref(0)
const PROBLEM_PAGE_SIZE = 20
const page = ref(1)

const problemQuery = ref('')
const problemTag = ref<string | null>(null)
const problemDifficulty = ref<string | null>(null)
const includeInactive = ref(false)

/** 见过的知识点标签（下拉选项来源，见文件头注释）。 */
const knownTags = ref<string[]>([])

const tagOptions = computed<SelectOption[]>(() => [
  { label: '全部知识点', value: '' },
  ...[...knownTags.value].sort().map((t) => ({ label: t, value: t }))
])

const difficultyOptions: SelectOption[] = [
  { label: '全部难度', value: '' },
  { label: '简单', value: 'easy' },
  { label: '中等', value: 'medium' },
  { label: '困难', value: 'hard' }
]

const DIFFICULTY_LABEL: Record<string, string> = {
  easy: '简单',
  medium: '中等',
  hard: '困难'
}

/** 并发保护：快速改筛选时，先发的请求可能后回来。 */
let requestSeq = 0

async function loadProblems(): Promise<void> {
  if (!isTeacher.value) return
  loading.value = true
  errorMsg.value = ''
  const seq = ++requestSeq
  try {
    const res = await listProblems({
      page: page.value,
      page_size: PROBLEM_PAGE_SIZE,
      q: problemQuery.value.trim() || undefined,
      knowledge_tag: problemTag.value || undefined,
      difficulty: (problemDifficulty.value || undefined) as
        | 'easy'
        | 'medium'
        | 'hard'
        | undefined,
      include_inactive: includeInactive.value
    })
    if (seq !== requestSeq) return
    problems.value = res.items
    problemTotal.value = res.total
    const seen = new Set([...knownTags.value, ...res.items.flatMap((p) => p.knowledge_tags)])
    knownTags.value = [...seen]
  } catch (err) {
    if (seq !== requestSeq) return
    errorMsg.value = messageOf(err, '无法加载题库')
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function reload(): void {
  page.value = 1
  void loadProblems()
}

let searchTimer: ReturnType<typeof setTimeout> | undefined
function onQueryChange(): void {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(reload, 300)
}

const pageCount = computed(() => Math.max(1, Math.ceil(problemTotal.value / PROBLEM_PAGE_SIZE)))

function goPrev(): void {
  if (page.value > 1) {
    page.value -= 1
    void loadProblems()
  }
}

function goNext(): void {
  if (page.value < pageCount.value) {
    page.value += 1
    void loadProblems()
  }
}

// --- 编辑 / 新建表单 -----------------------------------------------------

interface CaseDraft {
  input: string
  expected_output: string
  is_sample: boolean
}

interface ProblemForm {
  title: string
  knowledge_tags: string[]
  description: string
  input_format: string
  output_format: string
  samples: { input: string; output: string }[]
  time_limit: number
  memory_limit: number
  difficulty: 'easy' | 'medium' | 'hard' | null
  source: string
  test_cases: CaseDraft[]
}

function emptyForm(): ProblemForm {
  return {
    title: '',
    knowledge_tags: [],
    description: '',
    input_format: '',
    output_format: '',
    samples: [],
    time_limit: 10,
    memory_limit: 256,
    difficulty: null,
    source: '',
    test_cases: []
  }
}

const modalOpen = ref(false)
const editingId = ref<number | null>(null)
const form = ref<ProblemForm>(emptyForm())
const formBusy = ref(false)
const formError = ref('')

function openCreate(): void {
  editingId.value = null
  form.value = emptyForm()
  formError.value = ''
  modalOpen.value = true
}

async function openEdit(p: ProblemSummary): Promise<void> {
  formError.value = ''
  formBusy.value = true
  modalOpen.value = true
  try {
    const detail = await getProblem(p.id)
    // 用例要**全部**（含隐藏）——编辑的人就是出题人，隐藏用例只有这里
    // 能改。getProblem 只回可见用例，所以必须走 test-cases 端点。
    const cases = await getProblemTestCases(p.id)
    editingId.value = p.id
    form.value = {
      title: detail.title,
      knowledge_tags: [...detail.knowledge_tags],
      description: detail.description,
      input_format: detail.input_format ?? '',
      output_format: detail.output_format ?? '',
      samples: detail.samples.map((s) => ({
        input: (s as { input?: string }).input ?? '',
        output: (s as { output?: string }).output ?? ''
      })),
      time_limit: detail.time_limit,
      memory_limit: detail.memory_limit,
      difficulty: detail.difficulty as 'easy' | 'medium' | 'hard' | null,
      source: detail.source ?? '',
      test_cases: cases.test_cases.map((c) => ({
        input: c.input,
        expected_output: c.expected_output,
        is_sample: c.is_sample
      }))
    }
  } catch (err) {
    formError.value = messageOf(err, '无法加载题目详情')
    modalOpen.value = false
  } finally {
    formBusy.value = false
  }
}

function addSample(): void {
  form.value.samples.push({ input: '', output: '' })
}

function removeSample(index: number): void {
  form.value.samples.splice(index, 1)
}

function addCase(): void {
  form.value.test_cases.push({ input: '', expected_output: '', is_sample: false })
}

function removeCase(index: number): void {
  form.value.test_cases.splice(index, 1)
}

async function saveForm(): Promise<void> {
  const title = form.value.title.trim()
  if (!title) {
    formError.value = '标题不能为空'
    return
  }
  formBusy.value = true
  formError.value = ''
  const payload = {
    title,
    knowledge_tags: form.value.knowledge_tags,
    description: form.value.description,
    input_format: form.value.input_format || null,
    output_format: form.value.output_format || null,
    samples: form.value.samples,
    time_limit: form.value.time_limit,
    memory_limit: form.value.memory_limit,
    difficulty: form.value.difficulty,
    source: form.value.source || null,
    test_cases: form.value.test_cases
  }
  try {
    if (editingId.value === null) {
      await createProblem(payload)
    } else {
      await updateProblem(editingId.value, payload)
    }
    modalOpen.value = false
    reload()
  } catch (err) {
    formError.value = messageOf(err, '保存失败')
  } finally {
    formBusy.value = false
  }
}

// --- 行操作 -------------------------------------------------------------

function duplicate(p: ProblemSummary): void {
  dialog.warning({
    title: '复制题目',
    content: `复制「${p.title}」？副本会连隐藏用例一起复制，并以启用状态出现在列表里。`,
    positiveText: '复制',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await duplicateProblem(p.id)
        reload()
      } catch (err) {
        errorMsg.value = messageOf(err, '复制失败')
      }
    }
  })
}

function toggleActive(p: ProblemSummary): void {
  const turningOn = !p.is_active
  dialog.warning({
    title: turningOn ? '启用题目' : '停用题目',
    content: turningOn
      ? `「${p.title}」将重新出现在默认列表和选题器里。`
      : `「${p.title}」将从默认列表和选题器里消失（不删除，旧提交记录仍可解析）。`,
    positiveText: turningOn ? '启用' : '停用',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await setProblemActive(p.id, turningOn)
        reload()
      } catch (err) {
        errorMsg.value = messageOf(err, turningOn ? '启用失败' : '停用失败')
      }
    }
  })
}

// --- 生命周期（教师端面板惯例，见 useTeacherPanel）-----------------------

function reset(): void {
  problems.value = []
  problemTotal.value = 0
  page.value = 1
  errorMsg.value = ''
}

const panel = useTeacherPanel(loadProblems, reset)

async function signOut(): Promise<void> {
  await logout()
  panel.reset()
}
</script>

<template>
  <TeacherGate
    :ready="panel.ready.value"
    :is-teacher="panel.teacher.value"
    login-title="题库管理需要登录"
    login-body="增删改题目和判题用例记在教师账号上，登录后才会显示。"
    @sign-out="signOut"
  >
    <div class="problems-page">
      <PageHeader
        title="题库管理"
        subtitle="增删改查题目与判题用例；隐藏用例只在这里可见，练习与判题永远只见学生该见的部分"
        :loading="panel.teacher.value ? loading : undefined"
        :error="errorMsg"
        error-prefix="题库操作失败"
        @refresh="loadProblems"
      >
        <template #actions>
          <NButton type="primary" size="small" @click="openCreate">新建题目</NButton>
        </template>
      </PageHeader>

      <div class="filters">
        <NInput
          v-model:value="problemQuery"
          class="filter-search"
          placeholder="按标题搜索…"
          clearable
          @update:value="onQueryChange"
        />
        <NSelect
          v-model:value="problemTag"
          class="filter-select"
          :options="tagOptions"
          placeholder="知识点"
          @update:value="reload"
        />
        <NSelect
          v-model:value="problemDifficulty"
          class="filter-select"
          :options="difficultyOptions"
          placeholder="难度"
          @update:value="reload"
        />
        <NCheckbox
          v-model:checked="includeInactive"
          class="filter-inactive"
          @update:checked="reload"
        >
          含停用
        </NCheckbox>
      </div>

      <p v-if="!loading && problems.length === 0" class="list-empty">
        没有符合条件的题目
      </p>

      <div v-else class="list">
        <div class="list-head">
          <span class="col-id">#</span>
          <span class="col-title">标题</span>
          <span class="col-tags">知识点</span>
          <span class="col-diff">难度</span>
          <span class="col-limits">限制</span>
          <span class="col-active">状态</span>
          <span class="col-ops">操作</span>
        </div>

        <div v-for="p in problems" :key="p.id" class="list-row">
          <span class="col-id">{{ p.id }}</span>
          <span class="col-title" :title="p.title">{{ p.title }}</span>
          <span class="col-tags">
            <NTag v-for="t in p.knowledge_tags" :key="t" size="small" :bordered="false">
              {{ t }}
            </NTag>
            <span v-if="p.knowledge_tags.length === 0" class="muted">—</span>
          </span>
          <span class="col-diff">
            {{ p.difficulty ? DIFFICULTY_LABEL[p.difficulty] : '—' }}
          </span>
          <span class="col-limits">{{ p.time_limit }}s / {{ p.memory_limit }}MB</span>
          <span class="col-active">
            <NTag :type="p.is_active ? 'success' : 'warning'" size="small" :bordered="false">
              {{ p.is_active ? '启用' : '停用' }}
            </NTag>
          </span>
          <span class="col-ops">
            <NButton size="tiny" quaternary @click="openEdit(p)">编辑</NButton>
            <NButton size="tiny" quaternary @click="duplicate(p)">复制</NButton>
            <NButton size="tiny" quaternary @click="toggleActive(p)">
              {{ p.is_active ? '停用' : '启用' }}
            </NButton>
          </span>
        </div>
      </div>

      <div class="pager">
        <span class="pager-total">共 {{ problemTotal }} 题</span>
        <NButton size="tiny" :disabled="page <= 1" @click="goPrev">上一页</NButton>
        <span class="pager-page">{{ page }} / {{ pageCount }}</span>
        <NButton size="tiny" :disabled="page >= pageCount" @click="goNext">下一页</NButton>
      </div>

      <NModal
        v-model:show="modalOpen"
        preset="dialog"
        :title="editingId === null ? '新建题目' : `编辑题目 #${editingId}`"
        :show-icon="false"
        style="width: min(880px, 92vw)"
      >
        <p v-if="formError" class="form-error">{{ formError }}</p>

        <div class="form-section">
          <h3>基本信息</h3>
          <div class="form-grid">
            <label class="form-item span-2">
              <span class="form-label">标题 *</span>
              <NInput v-model:value="form.title" placeholder="题目标题" />
            </label>
            <label class="form-item">
              <span class="form-label">知识点</span>
              <NDynamicTags v-model:value="form.knowledge_tags" />
            </label>
            <label class="form-item">
              <span class="form-label">难度</span>
              <NSelect
                v-model:value="form.difficulty"
                :options="difficultyOptions.slice(1).map((o) => ({ label: o.label, value: o.value }))"
                placeholder="不标难度"
                clearable
              />
            </label>
            <label class="form-item">
              <span class="form-label">时间限制（秒）</span>
              <NInputNumber v-model:value="form.time_limit" :min="1" :max="60" />
            </label>
            <label class="form-item">
              <span class="form-label">内存限制（MB）</span>
              <NInputNumber v-model:value="form.memory_limit" :min="1" :max="2048" />
            </label>
            <label class="form-item span-2">
              <span class="form-label">来源</span>
              <NInput v-model:value="form.source" placeholder="LeetCode 1 / 课程A / 自编…" />
            </label>
          </div>
        </div>

        <div class="form-section">
          <h3>题面</h3>
          <div class="form-grid">
            <label class="form-item span-2">
              <span class="form-label">题目描述</span>
              <NInput v-model:value="form.description" type="textarea" :rows="5" placeholder="题面正文（Markdown 可用）" />
            </label>
            <label class="form-item">
              <span class="form-label">输入格式</span>
              <NInput v-model:value="form.input_format" type="textarea" :rows="2" />
            </label>
            <label class="form-item">
              <span class="form-label">输出格式</span>
              <NInput v-model:value="form.output_format" type="textarea" :rows="2" />
            </label>
          </div>
        </div>

        <div class="form-section">
          <div class="section-head">
            <h3>示例（学生可见）</h3>
            <NButton size="tiny" quaternary @click="addSample">+ 添加示例</NButton>
          </div>
          <div v-for="(s, i) in form.samples" :key="i" class="sample-row">
            <NInput v-model:value="s.input" type="textarea" :rows="2" placeholder="输入" />
            <NInput v-model:value="s.output" type="textarea" :rows="2" placeholder="输出" />
            <NButton size="tiny" quaternary type="error" @click="removeSample(i)">删除</NButton>
          </div>
          <p v-if="form.samples.length === 0" class="muted">还没有示例</p>
        </div>

        <div class="form-section">
          <div class="section-head">
            <h3>判题用例（不勾选 = 隐藏，学生看不到期望输出）</h3>
            <NButton size="tiny" quaternary @click="addCase">+ 添加用例</NButton>
          </div>
          <div v-for="(c, i) in form.test_cases" :key="i" class="case-row">
            <NInput v-model:value="c.input" type="textarea" :rows="2" placeholder="输入" />
            <NInput v-model:value="c.expected_output" type="textarea" :rows="2" placeholder="期望输出" />
            <NCheckbox v-model:checked="c.is_sample">学生可见</NCheckbox>
            <NButton size="tiny" quaternary type="error" @click="removeCase(i)">删除</NButton>
          </div>
          <p v-if="form.test_cases.length === 0" class="muted">
            还没有用例——保存后这道题无法判题（所有提交都会判错），记得添加。
          </p>
        </div>

        <template #footer>
          <div class="modal-footer">
            <NButton :disabled="formBusy" @click="modalOpen = false">取消</NButton>
            <NButton type="primary" :loading="formBusy" @click="saveForm">
              {{ editingId === null ? '创建' : '保存' }}
            </NButton>
          </div>
        </template>
      </NModal>
    </div>
  </TeacherGate>
</template>

<style scoped>
.problems-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 20px 24px 48px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.filter-search {
  width: 260px;
}

.filter-select {
  width: 160px;
}

.list {
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  overflow: hidden;
}

.list-head,
.list-row {
  display: grid;
  grid-template-columns: 44px 2.4fr 2fr 72px 110px 64px 190px;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
}

.list-head {
  height: 36px;
  font-size: 12px;
  color: var(--af-text-secondary);
  background: var(--af-overlay);
}

.list-row {
  min-height: 44px;
  border-top: 1px solid var(--af-border);
  font-size: 13px;
}

.list-row:hover {
  background: var(--af-overlay);
}

.col-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  overflow: hidden;
}

.col-ops {
  display: flex;
  gap: 2px;
}

.muted {
  color: var(--af-text-muted);
  font-size: 12px;
}

.list-empty {
  padding: 32px 0;
  text-align: center;
  color: var(--af-text-secondary);
}

.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
  font-size: 13px;
  color: var(--af-text-secondary);
}

.form-error {
  padding: 8px 12px;
  margin: 0 0 12px 0;
  font-size: 13px;
  color: var(--af-text-primary);
  background: color-mix(in srgb, var(--af-danger) 18%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-danger) 45%, transparent);
  border-radius: var(--af-radius-sm);
}

.form-section {
  margin-bottom: 20px;
}

.form-section h3 {
  margin: 0 0 10px 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-item.span-2 {
  grid-column: span 2;
}

.form-label {
  font-size: 12px;
  color: var(--af-text-secondary);
}

.sample-row,
.case-row {
  display: grid;
  gap: 8px;
  align-items: start;
  margin-bottom: 10px;
  padding: 10px;
  background: var(--af-overlay);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.sample-row {
  grid-template-columns: 1fr 1fr auto;
}

.case-row {
  grid-template-columns: 1fr 1fr auto auto;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
