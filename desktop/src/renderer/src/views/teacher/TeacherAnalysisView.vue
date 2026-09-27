<script setup lang="ts">
/**
 * TeacherAnalysisView —— 学情分析（2026-09-26 由 TeacherView 拆出）。
 *
 * 这个面板回答两个问题，按老师提问的顺序排：
 *
 *   下节课讲什么   → 错因分布（按出现次数降序）
 *   谁需要我       → 学生清单（按正确率升序）
 *
 * 顺序**就是**内容：老师不该自己去找到最大的那一行。两张表都由服务端
 * 排好序送来，这个页面**故意不重排**——第二次排序是对"什么重要"的第二
 * 个意见，两个意见迟早会漂开。
 *
 * 三层结构
 * --------
 *   班级层   错因分布、知识点掌握（只读）
 *   学生层   学生清单
 *   个例层   点一行展开的错题时间线
 *
 * 为什么下钻是一个面板而不是一个路由
 * ----------------------------------
 * `/teacher/students/3` 这样的 URL 意味着它可以被分享、被收藏——它不是，
 * 它只是老师回头看一眼某个学生。选中一行让班级语境留在屏幕上，而这正是
 * "从班级视图里看某一个学生"的全部意义。
 *
 * 为什么支持 `?student=<id>`（2026-09-26 新增）
 * --------------------------------------------
 * 这就是上面那条的边界："不可分享"针对的是**这个页面不能靠 URL 自己还原
 * 出一个学生**。但概览页的「需要关注」点一个人名，意图是**跳到这个面板
 * 并打开他**——不做这件事，那次点击就只是把老师丢到一个列表顶上，让他
 * 自己再找一遍。
 *
 * 所以 query 只是一个**一次性意图**：打开后立刻 `replace` 掉，不留在历史
 * 里，也不构成一个可收藏的状态。
 *
 * 未分析那一组
 * ------------
 * 分析还没落库的条目以 `label === null` 到达，渲染成「未分析」而不是被
 * 丢掉。这是有意的，而且是一个真实读数：如果未分析是最大的一组，说明
 * **分析落后了**；一个把这些行藏起来的控制台会把一次故障变成"风平浪静
 * 的一周"。
 */

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import TeacherGate from '../../components/TeacherGate.vue'
import PageHeader from '../../components/PageHeader.vue'
import BarChart from '../../components/BarChart.vue'
import PieChart from '../../components/PieChart.vue'
import { masteryColorFor } from '../../assets/tokens'
import { isTeacher, logout } from '../../api/session'
import {
  getErrorDistribution,
  getKnowledgeStats,
  getStudents,
  getStudentTimeline,
  type ErrorCount,
  type KnowledgeStatOut,
  type StudentOut,
  type TimelineEntryOut
} from '../../api/teacher'
import { useTeacherPanel } from './useTeacherPanel'
import { causeLabel, dateText, isUnanalysed, messageOf, percent, statusText } from './format'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const errorMsg = ref('')
const distribution = ref<ErrorCount[]>([])
const knowledge = ref<KnowledgeStatOut[]>([])
const students = ref<StudentOut[]>([])

/** 时间线开着的那名学生，`null` 表示班级视图。 */
const selected = ref<StudentOut | null>(null)
const timeline = ref<TimelineEntryOut[]>([])
const timelineLoading = ref(false)

/** 学生清单那一段，从关注列表跳进来时要滚到它。 */
const rosterSection = ref<HTMLElement | null>(null)

async function load(): Promise<void> {
  if (!isTeacher.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const [dist, know, stud] = await Promise.all([
      getErrorDistribution(),
      getKnowledgeStats(),
      getStudents()
    ])
    distribution.value = dist
    knowledge.value = know
    students.value = stud
    // 名册上已经没有的学生（被删、被改角色）不能把他的时间线留在屏幕
    // 上——那是关于一个已不存在的人的数据。
    if (selected.value && !stud.some((s) => s.user_id === selected.value?.user_id)) {
      selected.value = null
      timeline.value = []
    }
    // 从概览页点人名跳进来时带的意图。消费掉就抹掉，见文件头。
    const wanted = Number(route.query.student)
    if (Number.isFinite(wanted) && wanted > 0 && students.value.some((s) => s.user_id === wanted)) {
      await openStudent(students.value.find((s) => s.user_id === wanted)!)
      rosterSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    if (route.query.student !== undefined) {
      void router.replace({ name: 'teacher-analysis' })
    }
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载学情数据')
  } finally {
    loading.value = false
  }
}

function reset(): void {
  distribution.value = []
  knowledge.value = []
  students.value = []
  selected.value = null
  timeline.value = []
}

const panel = useTeacherPanel(load, reset)

async function signOut(): Promise<void> {
  await logout()
  panel.reset()
}

async function openStudent(student: StudentOut): Promise<void> {
  if (selected.value?.user_id === student.user_id) {
    selected.value = null
    timeline.value = []
    return
  }
  selected.value = student
  timeline.value = []
  timelineLoading.value = true
  try {
    timeline.value = await getStudentTimeline(student.user_id)
  } catch (err) {
    errorMsg.value = messageOf(err, `无法加载 ${student.display_name} 的错题记录`)
  } finally {
    timelineLoading.value = false
  }
}

/** 知识点，最弱的在前——和学生端画像页同一个顺序。 */
const sortedKnowledge = computed<KnowledgeStatOut[]>(() =>
  [...knowledge.value].sort((a, b) => a.accuracy - b.accuracy)
)

/**
 * 分析是不是落后了。
 *
 * 只在"未分析"是最大的一组时显示。这是控制台唯一该谈**它自己**而不是谈
 * 班级的场合：否则老师会把「未分析 12」读成一个教学主题。
 */
const analysisBehind = computed<boolean>(() => {
  if (distribution.value.length === 0) return false
  return isUnanalysed(distribution.value[0]) && distribution.value[0].count > 0
})

/** 错因总次数，给环形图的中心文本用。 */
const distributionTotal = computed<number>(() =>
  distribution.value.reduce((sum, row) => sum + row.count, 0)
)
</script>

<template>
  <div class="af-t-page">
    <div class="af-t-content">
      <PageHeader
        title="学情分析"
        subtitle="先看班级错因，再逐生往下看"
        :loading="panel.teacher.value ? loading : undefined"
        :error="errorMsg"
        error-prefix="无法加载学情"
        @refresh="load"
      />

      <TeacherGate
        :ready="panel.ready.value"
        :is-teacher="panel.teacher.value"
        login-title="学情分析需要登录"
        login-body="错因分布与学生清单由教师账号查看。学生侧的练习、错题本和画像不需要登录。"
        @sign-out="signOut"
      >
        <div
          v-if="loading && students.length === 0 && distribution.length === 0"
          class="af-t-placeholder"
        >
          加载学情数据中…
        </div>

        <template v-else>
          <!-- --- 下节课讲什么 --- -->
          <section class="af-t-section">
            <h2 class="af-t-section-title">错因分布</h2>
            <p class="af-t-section-note">按出现次数降序——最上面的那条就是下节课该讲的。</p>

            <div v-if="distribution.length === 0" class="af-t-empty">还没有错题记录。</div>

            <template v-else>
              <!-- 控制台在谈它自己，不是在谈班级。 -->
              <p v-if="analysisBehind" class="af-t-self-note">
                未分析的条目是当前最多的一组，说明分析还在补跑——这个数字不代表学生的问题。
              </p>
              <PieChart
                :items="distribution.map((row) => ({ name: causeLabel(row), value: row.count }))"
                :height="240"
                :value-formatter="(v: number) => `${v} 次`"
                :center-text="`共 ${distributionTotal} 次错因`"
                empty-title="暂无错题记录"
                empty-explanation="学生还没产生可分析的错题。"
              />
              <div class="af-t-table-scroll">
                <table class="af-t-table">
                  <thead>
                    <tr>
                      <th>错因</th>
                      <th class="af-t-num">次数</th>
                      <th>占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in distribution" :key="row.label ?? '__none__'">
                      <td>
                        <span class="cause" :class="{ 'cause-unknown': isUnanalysed(row) }">{{
                          causeLabel(row)
                        }}</span>
                      </td>
                      <td class="af-t-num">{{ row.count }}</td>
                      <td>
                        <div
                          class="af-t-progress-bar"
                          role="img"
                          :aria-label="`${causeLabel(row)} 占比 ${percent(row.share)}`"
                        >
                          <div
                            class="af-t-progress-fill"
                            :class="{ 'af-t-fill-unknown': isUnanalysed(row) }"
                            :style="{ width: `${Math.max(row.share * 100, 2)}%` }"
                          />
                        </div>
                        <span class="af-t-share-text">{{ percent(row.share) }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </section>

          <!-- --- 知识点 --- -->
          <section v-if="sortedKnowledge.length > 0" class="af-t-section">
            <h2 class="af-t-section-title">知识点掌握</h2>
            <p class="af-t-section-note">按正确率升序，最需要补的知识点在最上面。</p>
            <BarChart
              :items="sortedKnowledge.map((k) => ({ label: k.tag, value: k.accuracy }))"
              :max="1"
              :height="280"
              :color="(v: number) => masteryColorFor(v)"
              :value-formatter="(v: number) => percent(v)"
              empty-title="暂无知识点数据"
              empty-explanation="班级还没有知识点维度的提交记录。"
            />
            <div class="af-t-table-scroll">
              <table class="af-t-table">
                <thead>
                  <tr>
                    <th>知识点</th>
                    <th class="af-t-num">提交</th>
                    <th class="af-t-num">通过</th>
                    <th class="af-t-num">正确率</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="k in sortedKnowledge" :key="k.tag">
                    <td class="af-t-strong-cell">{{ k.tag }}</td>
                    <td class="af-t-num">{{ k.total }}</td>
                    <td class="af-t-num">{{ k.passed }}</td>
                    <td class="af-t-num" :style="{ color: masteryColorFor(k.accuracy) }">
                      {{ percent(k.accuracy) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- --- 谁需要我 --- -->
          <section ref="rosterSection" class="af-t-section">
            <h2 class="af-t-section-title">学生清单</h2>
            <p class="af-t-section-note">
              按正确率升序，最需要关注的学生在最上面；点一行可以看他/她的错题时间线。
            </p>

            <div v-if="students.length === 0" class="af-t-empty">
              还没有学生账号。去「名册与班级」添加。
            </div>

            <div v-else class="af-t-table-scroll">
              <table class="af-t-table">
                <thead>
                  <tr>
                    <th>学生</th>
                    <th class="af-t-num">提交</th>
                    <th class="af-t-num">正确率</th>
                    <th class="af-t-num">错题</th>
                    <th class="af-t-num">存疑</th>
                    <th>最薄弱知识点</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="s in students"
                    :key="s.user_id"
                    class="af-t-clickable-row"
                    :class="{ 'af-t-row-open': selected?.user_id === s.user_id }"
                    @click="openStudent(s)"
                  >
                    <td class="af-t-strong-cell">{{ s.display_name || s.username }}</td>
                    <td class="af-t-num">{{ s.submissions }}</td>
                    <td class="af-t-num" :style="{ color: masteryColorFor(s.accuracy) }">
                      {{ percent(s.accuracy) }}
                    </td>
                    <td class="af-t-num">{{ s.mistakes }}</td>
                    <td class="af-t-num">
                      <span v-if="s.flagged_submissions > 0" class="af-t-flag-badge">
                        {{ s.flagged_submissions }}
                      </span>
                      <span v-else>0</span>
                    </td>
                    <td>{{ s.weakest_tag ?? '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- --- 个例层：错题时间线 --- -->
            <div v-if="selected" class="drilldown">
              <div class="drilldown-head">
                <h3 class="drilldown-title">
                  {{ selected.display_name || selected.username }} 的错题时间线
                </h3>
                <NButton quaternary size="small" @click="openStudent(selected)">收起</NButton>
              </div>

              <div v-if="timelineLoading" class="af-t-placeholder">加载中…</div>
              <div v-else-if="timeline.length === 0" class="af-t-empty">
                这个学生还没有错题记录。
              </div>
              <ol v-else class="timeline">
                <li v-for="entry in timeline" :key="entry.id" class="timeline-item">
                  <span class="timeline-when">{{ dateText(entry.created_at) }}</span>
                  <span class="timeline-status">{{ statusText(entry.status) }}</span>
                  <span class="timeline-cause">
                    {{ entry.error_description || entry.error_type || '未分析' }}
                  </span>
                  <span class="timeline-problem"> 题目 {{ entry.problem_id ?? '—' }} </span>
                </li>
              </ol>
            </div>
          </section>
        </template>
      </TeacherGate>
    </div>
  </div>
</template>

<script lang="ts">
export default { name: 'TeacherAnalysisView' }
</script>

<style scoped>
.cause {
  font-weight: 600;
}

/* 未分析那一组是一处缺席，不是一个原因：虚线下划线而不是颜色，
 * 免得被读成一次诊断。 */
.cause-unknown {
  font-weight: 500;
  color: var(--af-text-secondary);
  border-bottom: 1px dashed var(--af-border-strong);
}

/* --- 个例层 --- */
.drilldown {
  margin-top: 12px;
  padding: 14px 16px;
  background: var(--af-surface);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-md);
}

.drilldown-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.drilldown-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.timeline {
  margin: 0;
  padding: 0;
  list-style: none;
}

.timeline-item {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 7px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--af-border);
}

.timeline-item:last-child {
  border-bottom: none;
}

.timeline-when {
  min-width: 120px;
  color: var(--af-text-muted);
  font-variant-numeric: tabular-nums;
}

.timeline-status {
  min-width: 72px;
  color: var(--af-text-secondary);
}

.timeline-cause {
  flex: 1 1 auto;
}

.timeline-problem {
  color: var(--af-text-muted);
}
</style>
