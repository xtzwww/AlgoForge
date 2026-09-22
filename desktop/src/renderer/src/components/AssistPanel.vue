<script setup lang="ts">
/**
 * 辅助面板 —— 编辑器旁边的 AI（阶段 5）。
 *
 * 四件事按档位出现
 * ----------------
 * | 档 | 补全 | 对话 | 写代码 | 整解 |
 * |----|------|------|--------|------|
 * | 1  | 本机 |  —   |   —    |  —   |
 * | 2  | 联网 |  —   |   —    |  —   |
 * | 3  | 联网 | 提示 |   —    |  —   |
 * | 4  | 联网 | 可写 |  可插  |  —   |
 * | 5  | 联网 | 可写 |  可插  | 可给 |
 *
 * **按钮的隐藏只是体验，规则在服务端。** 这里按 ``policy`` 决定显示什么，
 * 是为了让学生一眼看出"这次作业允许到什么程度"，而不是为了拦住谁 ——
 * 改前端 JS 就能绕过的限制不算限制，真正的裁决在
 * `app/assistance_service.py`，那里连"拒绝要发生在调用模型之前"都钉住了。
 *
 * 为什么被拒不是红色报错
 * ----------------------
 * 服务端对越档请求返回 **200 + `allowed=false` + `reason`**。档位是教学
 * 设置，不是故障：学生点到被禁用的功能，该看到的是一句解释（"这次作业
 * 只用本地补全"），而不是一个需要反馈给老师的错误。所以这里把 `reason`
 * 当普通消息渲染，位置和成功回复一致。
 *
 * 三个动作的产物去向不同，这是刻意的
 * ----------------------------------
 * - 补全 → 只给候选，**不自动写进编辑器**。连四档的"插入"也要学生点一下
 *   候选才落进去：AI 写代码和 AI 改代码是两件事，后者会覆盖学生自己写的。
 * - 对话 → 只进面板，永不进编辑器（三档更是由服务端保证内容不是代码）。
 * - 插入 / 整解 → 通过 `insert` 事件交给页面，由页面决定怎么放进缓冲区。
 *   面板自己拿不到编辑器，也就不可能绕过页面的替换确认。
 *
 * 复用 `editTrace` 的语义：面板不自己去数改动，它发事件，页面统一处理。
 */

import { computed, ref, watch } from 'vue'
import {
  chat as requestChat,
  complete as requestComplete,
  insert as requestInsert,
  solution as requestSolution,
  type AssistancePolicy,
  type AssistResponse
} from '../api/assistance'

const props = withDefaults(
  defineProps<{
    /** 当前档位规则。为 null 表示还没取到，此时面板只是禁用而不是消失。 */
    policy: AssistancePolicy | null
    /** 作业 id —— 服务端据此查档位。省略即默认档。 */
    assignmentId?: number | null
    problemId?: number | null
    language?: string
    /** 光标之前的文本，供一档摘标识符。 */
    prefix: string
    /** 编辑器现有内容，供四档的"接着写"。 */
    currentCode: string
    /** 题干。五档的整解与对话都用它。 */
    statement: string
    /** 判题或环境正在忙时禁用，避免与运行抢焦点。 */
    busy?: boolean
  }>(),
  {
    assignmentId: null,
    problemId: null,
    language: 'cpp',
    busy: false
  }
)

const emit = defineEmits<{
  /** 把一段代码放进编辑器。页面负责它怎么落 —— 追加还是替换。 */
  (e: 'insert', code: string): void
}>()

/** 面板收起时只剩标题栏，给编辑器让出宽度。 */
const collapsed = ref(false)

const question = ref('')
const instruction = ref('')
const pending = ref(false)
/** 最后一次回复。四档下这可能是代码，页面会把它渲染在等宽块里。 */
const reply = ref<AssistResponse | null>(null)
const failure = ref('')

const level = computed<number>(() => props.policy?.level ?? 0)
const ready = computed<boolean>(() => props.policy !== null)
const disabled = computed<boolean>(() => props.busy === true || pending.value)

/** 一档的本机补全也会返回 candidates，所以两者都要看。 */
const candidates = computed<string[]>(() => reply.value?.candidates ?? [])
const hasReply = computed<boolean>(
  () => reply.value !== null && (reply.value.content !== '' || candidates.value.length > 0)
)

/** 切换题目 / 作业就清空 —— 上一题的对话接着显示会误导。 */
watch(
  () => [props.assignmentId, props.problemId],
  () => {
    reply.value = null
    failure.value = ''
    question.value = ''
    instruction.value = ''
  }
)

function common(): { assignment_id?: number; problem_id?: number; language?: string } {
  return {
    // 只有真的绑在作业上才传。传 null 会被服务端当成"没有作业"，与省略等价，
    // 但显式传 null 会让 pydantic 收到一个 None 而不是缺省值 —— 少一层歧义。
    ...(props.assignmentId === null ? {} : { assignment_id: props.assignmentId }),
    ...(props.problemId === null ? {} : { problem_id: props.problemId }),
    language: props.language
  }
}

async function run(action: () => Promise<AssistResponse>): Promise<void> {
  pending.value = true
  failure.value = ''
  try {
    reply.value = await action()
  } catch (err) {
    // 这里才是真故障（网络、服务不可用）。被拒不算 —— 那是 200。
    failure.value = err instanceof Error ? err.message : 'AI 暂时不可用'
    reply.value = null
  } finally {
    pending.value = false
  }
}

/**
 * 补全。
 *
 * 一档拿到的是一串候选词，二档起是模型给的一段代码 —— 两者都以
 * `candidates` / `content` 的形式展示，由学生点「插入」决定要不要。
 * **不自动落进编辑器**：见组件 docstring。
 */
async function askCompletion(): Promise<void> {
  await run(() => requestComplete({ ...common(), prefix: props.prefix }))
}

async function askChat(): Promise<void> {
  if (question.value.trim() === '') return
  const q = question.value
  await run(() => requestChat({ ...common(), question: q, statement: props.statement }))
  // 成功后清空输入框，失败保留 —— 重试时不该重新打一遍。
  if (failure.value === '') question.value = ''
}

async function askInsert(): Promise<void> {
  if (instruction.value.trim() === '') return
  const text = instruction.value
  await run(() =>
    requestInsert({ ...common(), instruction: text, current_code: props.currentCode })
  )
  if (failure.value === '') instruction.value = ''
}

async function askSolution(): Promise<void> {
  await run(() => requestSolution({ ...common(), statement: props.statement }))
}

/** 把代码交给页面。裁剪交给服务端，这里不做二次处理。 */
function useCode(code: string): void {
  if (code.trim() === '') return
  emit('insert', code)
}
</script>

<template>
  <aside class="assist-panel" :class="{ collapsed }">
    <header class="assist-head">
      <button
        class="assist-toggle"
        type="button"
        :aria-expanded="collapsed ? 'false' : 'true'"
        :title="collapsed ? '展开 AI 辅助' : '收起 AI 辅助'"
        @click="collapsed = !collapsed"
      >
        <span class="assist-icon" aria-hidden="true">{{ collapsed ? '‹' : '›' }}</span>
        <span v-if="!collapsed" class="assist-title">AI 辅助</span>
      </button>
      <span v-if="!collapsed && ready" class="assist-level" :title="policy?.description">
        {{ level }} 档 · {{ policy?.student_label }}
      </span>
      <!--
        取不到规则时说"读取中"而不是"1 档"：默认到最严看起来像产品决定，
        其实只是请求还没回来。
      -->
      <span v-else-if="!collapsed" class="assist-level assist-level-unknown">档位读取中…</span>
    </header>

    <div v-if="!collapsed" class="assist-body">
      <p class="assist-hint">{{ policy?.description ?? '正在读取本次作业允许的辅助强度…' }}</p>

      <div class="assist-actions">
        <!--
          一档也要有补全按钮，只是它不联网。按 `completion_uses_model`
          改文案而不是藏起来 —— 一档的学生最该知道"这是本机给的"。
        -->
        <button
          class="btn btn-secondary btn-sm"
          type="button"
          :disabled="disabled || !ready"
          :title="policy?.completion_uses_model ? '由 AI 给出接下来的写法' : '本机词表补全，不联网'"
          @click="askCompletion"
        >
          {{ policy?.completion_uses_model ? 'AI 补全' : '本机补全' }}
        </button>

        <button
          v-if="policy?.allows_full_solution"
          class="btn btn-secondary btn-sm"
          type="button"
          :disabled="disabled || statement.trim() === ''"
          title="让 AI 给出这道题的完整解法"
          @click="askSolution"
        >
          完整解法
        </button>
      </div>

      <!-- 对话：三档起。三档只给提示，四档起可以写。 -->
      <div v-if="policy?.allows_chat" class="assist-chat">
        <label class="assist-label" for="assist-question">
          问 AI
          <span v-if="policy.chat_is_hint_only" class="assist-tag">只给提示</span>
        </label>
        <textarea
          id="assist-question"
          v-model="question"
          class="assist-input"
          rows="2"
          placeholder="例如：这道题为什么用前缀和？"
          :disabled="disabled"
          @keydown.ctrl.enter.prevent="askChat"
          @keydown.meta.enter.prevent="askChat"
        />
        <button
          class="btn btn-secondary btn-sm"
          type="button"
          :disabled="disabled || question.trim() === ''"
          @click="askChat"
        >
          提问
        </button>
      </div>

      <!-- 写一段代码：四档起。 -->
      <div v-if="policy?.allows_code_insertion" class="assist-chat">
        <label class="assist-label" for="assist-instruction">让 AI 写一段</label>
        <textarea
          id="assist-instruction"
          v-model="instruction"
          class="assist-input"
          rows="2"
          placeholder="例如：写一个读入 n 然后建数组的循环"
          :disabled="disabled"
          @keydown.ctrl.enter.prevent="askInsert"
          @keydown.meta.enter.prevent="askInsert"
        />
        <button
          class="btn btn-secondary btn-sm"
          type="button"
          :disabled="disabled || instruction.trim() === ''"
          @click="askInsert"
        >
          生成
        </button>
      </div>

      <!-- 两个位置都不允许任何辅助的档位不该存在，但真出现了要说清。 -->
      <p
        v-if="
          ready &&
          !policy?.allows_chat &&
          !policy?.allows_code_insertion &&
          !policy?.completion_uses_model
        "
        class="assist-note"
      >
        这一档只提供本机补全，AI 对话与代写不可用。
      </p>

      <p v-if="pending" class="assist-note">AI 思考中…</p>
      <p v-if="failure" class="assist-note assist-note-error">{{ failure }}</p>

      <!--
        被拒与服务端的解释。刻意用与成功回复相同的版式：它是一句话，
        不是一个错误。
      -->
      <div v-if="reply" class="assist-reply">
        <p v-if="!reply.allowed" class="assist-reply-reason">{{ reply.reason }}</p>

        <template v-else>
          <p v-if="reply.downgraded" class="assist-reply-flag">
            这一档只给提示，AI 的回复已自动降级。
          </p>
          <pre v-if="reply.content" class="assist-code">{{ reply.content }}</pre>

          <ul v-if="candidates.length" class="assist-candidates">
            <li v-for="(item, i) in candidates" :key="i">
              <button class="assist-candidate" type="button" @click="useCode(item)">
                {{ item }}
              </button>
            </li>
          </ul>

          <!-- 能写进缓冲区的东西才给按钮；提示型回复进不了编辑器。 -->
          <button
            v-if="reply.content && (reply.action === 'insert' || reply.action === 'full_solution')"
            class="btn btn-primary btn-sm"
            type="button"
            @click="useCode(reply.content)"
          >
            插入到编辑器
          </button>

          <p v-if="!hasReply" class="assist-note">{{ reply.reason }}</p>
        </template>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.assist-panel {
  display: flex;
  flex-direction: column;
  width: 260px;
  flex-shrink: 0;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  overflow: hidden;
}

.assist-panel.collapsed {
  width: 36px;
}

.assist-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px;
  border-bottom: 1px solid var(--af-border);
}

.assist-panel.collapsed .assist-head {
  align-items: center;
  border-bottom: none;
}

.assist-toggle {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  padding: 2px 4px;
  font-size: 11px;
  color: var(--af-text-muted);
  background: transparent;
  border: none;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.assist-toggle:hover {
  color: var(--af-text-primary);
  background: var(--af-elevated);
}

.assist-panel.collapsed .assist-toggle {
  flex-direction: column;
  padding: 8px 2px;
}

.assist-title {
  white-space: nowrap;
}

.assist-level {
  padding: 0 4px;
  font-size: 11px;
  color: var(--af-text-muted);
}

.assist-level-unknown {
  font-style: italic;
}

.assist-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px;
}

.assist-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.assist-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.assist-chat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.assist-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--af-text-secondary);
}

.assist-tag {
  padding: 0 5px;
  font-size: 10px;
  color: var(--af-warning, #e8a33d);
  border: 1px solid var(--af-warning, #e8a33d);
  border-radius: var(--af-radius-sm);
}

.assist-input {
  width: 100%;
  padding: 5px 6px;
  font-family: inherit;
  font-size: 12px;
  color: var(--af-text-primary);
  background: var(--af-bg);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  resize: vertical;
}

.assist-input:disabled {
  opacity: 0.6;
}

.assist-note {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.assist-note-error {
  color: var(--af-text-primary);
}

.assist-reply {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--af-border);
}

.assist-reply-reason,
.assist-reply-flag {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-secondary);
}

.assist-reply-flag {
  color: var(--af-warning, #e8a33d);
}

.assist-code {
  margin: 0;
  padding: 6px;
  max-height: 220px;
  overflow: auto;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-primary);
  background: var(--af-bg);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  white-space: pre-wrap;
  word-break: break-word;
}

.assist-candidates {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.assist-candidate {
  width: 100%;
  padding: 3px 6px;
  font-family: monospace;
  font-size: 11px;
  text-align: left;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.assist-candidate:hover {
  border-color: var(--af-accent, #3b82f6);
}

@media (max-width: 899px) {
  /* 堆叠布局下横向空间不再紧张，面板改为整宽。 */
  .assist-panel {
    width: auto;
  }

  .assist-panel.collapsed {
    width: auto;
  }
}
</style>
