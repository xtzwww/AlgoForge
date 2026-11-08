<script setup lang="ts">
/**
 * TeacherReviewView —— 存疑提交（2026-09-26 独立成屏）。
 *
 * 为什么从概览里搬出来
 * --------------------
 * 它是一次**批阅型事务**：要一条条处理完，中途会切走再回来。留在概览页
 * 的第六块意味着每次滚过去都看到它、每次都没处理完，而且被一圈数字包围、
 * 容易误点。概览上留一张可点的卡片做入口就够了。
 *
 * 编辑行为特征提示这些提交的代码可能不是当场写出来的。这只是**需要看一
 * 眼**的线索，不是判定——依据就在每条下面，由老师判断。
 *
 * 判断完请点确认或驳回：驳回的是误报，会退出这个列表；确认的会留在这里，
 * 方便事后复查。判错了可以撤销——一次误点必须能找回来，否则一次"驳回"
 * 就会永远藏掉一条真实的标记。
 */

import { ref } from 'vue'
import { NAlert, NButton, NCheckbox } from 'naive-ui'
import TeacherGate from '../../components/TeacherGate.vue'
import PageHeader from '../../components/PageHeader.vue'
import { getFlaggedSubmissions, reviewFlag, type FlaggedSubmission } from '../../api/teacher'
import { useTeacherPanel } from './useTeacherPanel'
import { dateText, messageOf, reviewText, statusText } from './format'

const loading = ref(false)
const errorMsg = ref('')
const flagged = ref<FlaggedSubmission[]>([])

/**
 * 队列要不要也显示老师已经判过的那些。
 *
 * 默认关：把一条判掉，就是让它停止请求关注。
 */
const showReviewed = ref(false)

/** 正在保存判定的那一行，好让只有它被禁用。 */
const reviewingId = ref<number | null>(null)
const reviewError = ref('')

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    flagged.value = await getFlaggedSubmissions(50, showReviewed.value)
  } catch (err) {
    errorMsg.value = messageOf(err, '无法加载存疑提交')
  } finally {
    loading.value = false
  }
}

function reset(): void {
  flagged.value = []
  showReviewed.value = false
}

useTeacherPanel(load, reset)

/**
 * 记下一次判定，然后重新加载。
 *
 * 重载而不是就地改那一行：一次判定会挪动三个数字——这个队列、概览里的
 * `open_flags`、以及这名学生自己的存疑计数。就地更新那一行会让另外两个
 * 过期，而一个自相矛盾的控制台比一个要多等一瞬的更糟。
 */
async function submitReview(
  item: FlaggedSubmission,
  verdict: 'confirmed' | 'dismissed' | 'cleared'
): Promise<void> {
  reviewingId.value = item.id
  reviewError.value = ''
  try {
    await reviewFlag(item.id, verdict)
    await load()
  } catch (err) {
    reviewError.value = messageOf(err, '无法保存判定')
  } finally {
    reviewingId.value = null
  }
}
</script>

<template>
  <div class="af-t-page">
    <div class="af-t-content">
      <PageHeader
        title="存疑提交"
        :subtitle="flagged.length > 0 ? `${flagged.length} 条待看` : '都看过了'"
        :loading="loading"
        :error="errorMsg"
        error-prefix="无法加载存疑提交"
        @refresh="load"
      />

      <TeacherGate>
        <section class="af-t-section">
          <p class="af-t-section-note">
            编辑行为特征提示这些提交的代码可能不是当场写出来的。这只是<strong>需要看一眼</strong>的线索，不是判定——点开可以看到具体依据，由你判断。
          </p>
          <p class="af-t-section-note">
            判断完请点<strong>确认</strong>或<strong>驳回</strong>：驳回的是误报，会退出这个列表；
            确认的会留在这里，方便你事后复查。判错了可以撤销。
          </p>

          <NCheckbox v-model:checked="showReviewed" class="queue-toggle" @update:checked="load">
            显示已复核的
          </NCheckbox>

          <NAlert v-if="reviewError" type="error" :bordered="false" class="queue-error">
            {{ reviewError }}
          </NAlert>

          <div v-if="flagged.length === 0 && !loading" class="af-t-empty">
            {{ showReviewed ? '没有存疑提交记录。' : '暂无存疑提交——都看过了。' }}
          </div>

          <ul v-else class="flagged-list">
            <li v-for="item in flagged" :key="item.id" class="flagged-item">
              <div class="flagged-head">
                <span class="flagged-who">{{ item.student || '未归属' }}</span>
                <span class="flagged-when">{{ dateText(item.created_at) }}</span>
                <span class="flagged-status">{{ statusText(item.status) }}</span>
                <span
                  v-if="reviewText(item.review?.verdict)"
                  class="review-badge"
                  :class="item.review?.verdict"
                  >{{ reviewText(item.review?.verdict) }}</span
                >
              </div>
              <p class="flagged-summary">{{ item.process?.summary ?? '' }}</p>
              <!-- 依据。一个老师查不了的标记，他要么照单全收要么无视。 -->
              <details v-if="item.process?.reasons?.length" class="flagged-evidence">
                <summary>查看依据</summary>
                <ul>
                  <li v-for="reason in item.process.reasons" :key="reason">{{ reason }}</li>
                </ul>
              </details>
              <!-- C2：风格一致性是独立的第二次阅读，可能与过程特征同时
                   存在，也可能单独触发。两种证据并排，而不是合并成一句
                   —— 合并会让其中一种变得无法查证。 -->
              <template v-if="item.style">
                <p class="flagged-summary style-summary">{{ item.style.summary }}</p>
                <details
                  v-if="item.style.reasons?.length"
                  class="flagged-evidence"
                >
                  <summary>查看风格依据</summary>
                  <ul>
                    <li v-for="reason in item.style.reasons" :key="reason">{{ reason }}</li>
                  </ul>
                </details>
              </template>
              <!-- 复核动作：这才是让标记成为一个提示而不是一纸判决的东西。
                   一个谁都不能反驳的标记，老师只能照单全收。 -->
              <div class="review-actions">
                <NButton
                  size="small"
                  :loading="reviewingId === item.id"
                  @click="submitReview(item, 'confirmed')"
                >
                  确认可疑
                </NButton>
                <NButton
                  size="small"
                  :loading="reviewingId === item.id"
                  @click="submitReview(item, 'dismissed')"
                >
                  驳回（误报）
                </NButton>
                <!-- 「撤销判定」是一次误点的撤销，不是第三种判定——
                     要够得着，但不能和两个真选择抢眼，所以是虚线边框。 -->
                <NButton
                  v-if="item.review?.verdict"
                  size="small"
                  dashed
                  :loading="reviewingId === item.id"
                  @click="submitReview(item, 'cleared')"
                >
                  撤销判定
                </NButton>
              </div>
            </li>
          </ul>
        </section>
      </TeacherGate>
    </div>
  </div>
</template>

<script lang="ts">
export default { name: 'TeacherReviewView' }
</script>

<style scoped>
.queue-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--af-text-secondary);
  cursor: pointer;
}

.queue-error {
  margin-bottom: 8px;
}

.flagged-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.flagged-item {
  padding: 12px 14px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.flagged-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
  font-size: 13px;
}

.flagged-who {
  font-weight: 600;
}

.flagged-when,
.flagged-status {
  font-size: 12px;
  color: var(--af-text-muted);
}

.flagged-summary {
  margin: 6px 0 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

.style-summary {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--af-border);
}

.flagged-evidence {
  margin-top: 8px;
  font-size: 12px;
}

.flagged-evidence summary {
  cursor: pointer;
  color: var(--af-text-muted);
}

.flagged-evidence ul {
  margin: 6px 0 0 0;
  padding-left: 18px;
  line-height: 1.7;
  color: var(--af-text-secondary);
}

.review-badge {
  padding: 1px 7px;
  font-size: 11px;
  border: 1px solid var(--af-border-strong);
  border-radius: 999px;
  color: var(--af-text-secondary);
}

/* 已确认沿用强调色的家族但不用它的填充：它是老师得出的结论，不是一条警报。 */
.review-badge.confirmed {
  color: var(--af-accent);
  border-color: var(--af-accent);
}

/* 已驳回刻意做得安静——它是一行不再请求关注的东西，
   给它警报的样式就是把注意力又放回去了。 */
.review-badge.dismissed {
  color: var(--af-text-muted);
}

.review-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
</style>
