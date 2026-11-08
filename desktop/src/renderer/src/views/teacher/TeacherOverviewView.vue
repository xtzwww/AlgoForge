<script setup lang="ts">
/**
 * TeacherOverviewView —— 教学概览（2026-09-26 由 TeacherView 拆出）。
 *
 * 这个面板回答一个问题：**我现在需不需要做点什么？** 它曾经是那个
 * 1600 行页面顶部的两块（指标卡 + 需要关注），后面还跟着七块内容。
 * 于是"读三秒就该有答案"的判断被摊成了一次通读——这才是学情看板
 * 显得杂乱的真正原因，不是功能多。
 *
 * 一屏原则
 * --------
 * 指标卡 + 需要关注要塞进一屏。数字区固定，只有关注列表可滚。所以
 * `.af-t-content-fill` 给内容栏一个最小高度，列表用 `flex: 1` 吃掉
 * 剩余空间、内部滚动——**列表不能把下面的东西顶出屏幕**，否则"一屏"
 * 就只是"内容少的时候的一屏"。
 *
 * 指标卡可以点（2026-09-26 新增）
 * ------------------------------
 * 拆分前五张卡只是数字：看到"存疑提交 3"之后要自己去侧边栏找那一页。
 * 现在每张卡是通往它明细页的入口。这不是装饰——一张点不动的卡和一个
 * 能点的入口，差别是老师要不要记住"那个页面叫什么"。
 *
 * 学生数和总提交**故意不可点**：全量学生与提交明细没有对应的面板
 * （名册页有名单，但那是账号管理，不是同一件事）。给它们加一个跳到
 * 半相关页面的入口，比不可点更糟。
 */

import { ref } from 'vue'
import AttentionAlerts from '../../components/AttentionAlerts.vue'
import TeacherGate from '../../components/TeacherGate.vue'
import PageHeader from '../../components/PageHeader.vue'
import { masteryColorFor } from '../../assets/tokens'
import { getAlerts, getOverview, type AlertOut, type ClassOverview } from '../../api/teacher'
import { useTeacherPanel } from './useTeacherPanel'
import { messageOf, num, percent } from './format'

const loading = ref(false)
const errorMsg = ref('')
const overview = ref<ClassOverview | null>(null)
/** 谁需要关注，以及为什么。空是合法答案。 */
const alerts = ref<AlertOut[]>([])

/**
 * 拉数据。
 *
 * **不再有 `if (!isTeacher.value) return`（2026-10-04）。** 那道保险是
 * 八个副本中的一个，现在由 `router/guards/auth.ts` 在导航阶段保证：
 * 能渲染这一页 = 是教师。`useTeacherPanel` 仍然只在会话就绪且是教师时
 * 触发它，所以"未就绪时不拉数据"这条不变量的**执行点**也只有一个。
 */
async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    const [ov, warn] = await Promise.all([getOverview(), getAlerts()])
    overview.value = ov
    alerts.value = warn
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载学情数据')
  } finally {
    loading.value = false
  }
}

function reset(): void {
  overview.value = null
  alerts.value = []
}

/**
 * 加载时机由共用的 hook 负责（会话就绪 + 是教师才跑 `load`）。
 *
 * 返回值不再需要：模板原先用 `panel.teacher.value` 给 `PageHeader` 的
 * `loading` 加一道"不是教师就不转圈"的保险，现在守卫已经保证是教师，
 * 直接传 `loading` 即可。`useTeacherPanel` 仍做它那份工作，只是调用方
 * 不接它的返回值。
 */
useTeacherPanel(load, reset)
</script>

<template>
  <div class="af-t-page">
    <div class="af-t-content af-t-content-fill">
      <PageHeader
        title="教学概览"
        :subtitle="overview ? `${overview.students} 名学生 · ${overview.submissions} 次提交` : ''"
        :loading="loading"
        :error="errorMsg"
        error-prefix="无法加载学情"
        @refresh="load"
      />

      <TeacherGate>
        <div v-if="loading && !overview" class="af-t-placeholder">加载学情数据中…</div>

        <template v-else-if="overview">
          <!--
            指标卡。四张是明确通往某个面板的；前两张只陈述数字，
            理由见文件头的文档。
          -->
          <div class="summary">
            <div class="summary-card">
              <span class="summary-label">学生数</span>
              <span class="summary-value">{{ num(overview.students) }}</span>
              <span class="summary-hint">已建账号</span>
            </div>

            <div class="summary-card">
              <span class="summary-label">总提交</span>
              <span class="summary-value">{{ num(overview.submissions) }}</span>
              <span class="summary-hint">含重复提交</span>
            </div>

            <RouterLink class="summary-card summary-link" :to="{ name: 'teacher-analysis' }">
              <span class="summary-label">整体正确率</span>
              <span class="summary-value" :style="{ color: masteryColorFor(overview.accuracy) }">
                {{ percent(overview.accuracy) }}
              </span>
              <span class="summary-hint"
                >{{ overview.accepted }} / {{ overview.submissions }} 通过</span
              >
              <span class="summary-go">看错因分布 →</span>
            </RouterLink>

            <RouterLink class="summary-card summary-link" :to="{ name: 'teacher-analysis' }">
              <span class="summary-label">待复查错题</span>
              <span class="summary-value">{{ num(overview.open_mistakes) }}</span>
              <span class="summary-hint">已分析 {{ overview.analysed_mistakes }} 条</span>
              <span class="summary-go">看知识点 →</span>
            </RouterLink>

            <RouterLink class="summary-card summary-link" :to="{ name: 'teacher-review' }">
              <span class="summary-label">存疑提交</span>
              <span class="summary-value">{{ num(overview.open_flags) }}</span>
              <!-- 两个数字都要给出，因为它们说的不是一件事：第一个是还有
                   多少要读，第二个是检测器有多爱报警。只看第一个的话，
                   一个逢交必报、报了又被逐条驳回的规则，看起来像是从不触发。 -->
              <span class="summary-hint">共标记 {{ num(overview.flagged_submissions) }} 条</span>
              <span class="summary-go">去复核 →</span>
            </RouterLink>
          </div>

          <!-- 谁现在就需要我（阶段 6）。
               列表自己滚，不把页面撑长——这是"一屏看完"的实现方式。 -->
          <section v-if="alerts.length > 0" class="alerts-section">
            <h2 class="af-t-section-title">需要关注</h2>
            <p class="af-t-section-note">
              每条都写明了触发它的依据。这不是评分——没有「风险 87%」这种数字，
              因为没法追问的数字你也不会信。
            </p>
            <!--
              列表放在自己的组件里，好让它脱离这个视图单独测：它不持有
              状态、不发请求，只渲染递给它的东西。"什么都不显示"由上面的
              `v-if` 决定——渲染一个空的「需要关注」标题是噪音。
            -->
            <div class="alerts-body">
              <AttentionAlerts
                :alerts="alerts"
                @select="
                  (a) =>
                    $router.push({
                      name: 'teacher-analysis',
                      query: { student: String(a.user_id) }
                    })
                "
              />
            </div>
          </section>

          <p v-else class="af-t-placeholder-inline all-clear">
            暂时没有需要立刻处理的学生。详细数据在各面板里。
          </p>
        </template>
      </TeacherGate>
    </div>
  </div>
</template>

<script lang="ts">
export default { name: 'TeacherOverviewView' }
</script>

<style scoped>
/* --- 指标卡 --- */
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.summary-card {
  flex: 1 1 140px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 14px 12px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  text-decoration: none;
  color: inherit;
}

/* 可点的卡片才有悬停反应。不可点的那两张保持静止，免得暗示一个
 * 不存在的点击。 */
.summary-link {
  transition:
    border-color var(--af-duration-fast) var(--af-ease),
    background var(--af-duration-fast) var(--af-ease),
    transform var(--af-duration-fast) var(--af-ease-pop);
}

.summary-link:hover {
  border-color: var(--af-accent);
  background: var(--af-elevated);
  transform: translateY(-2px);
}

.summary-label {
  font-size: 12px;
  color: var(--af-text-secondary);
}

.summary-value {
  font-size: 24px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.summary-hint {
  font-size: 11px;
  color: var(--af-text-muted);
}

/* 只在可点的卡上出现，且很轻：它是提示，不是第三个数字。 */
.summary-go {
  margin-top: 4px;
  font-size: 11px;
  color: var(--af-accent);
  opacity: 0;
  transition: opacity var(--af-duration-fast) var(--af-ease);
}

.summary-link:hover .summary-go,
.summary-link:focus-visible .summary-go {
  opacity: 1;
}

/* --- 关注列表 --- */
.alerts-section {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  margin-bottom: 0;
}

/* `min-height: 0` 是关键：没有它，flex 子项的最小高度是内容高度，
   这里的 `overflow-y: auto` 永远不会真的滚动。 */
.alerts-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.all-clear {
  margin-top: 4px;
}
</style>
