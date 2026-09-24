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

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  chat as requestChat,
  complete as requestComplete,
  insert as requestInsert,
  listModels,
  setModel,
  solution as requestSolution,
  type AssistancePolicy,
  type AssistResponse,
  type ModelOption
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

// --- 对话历史（PU-13）-------------------------------------------------
//
// 面板过去是「一问一答」：`reply` 存最后一个回答，下一次提问把它覆盖掉。
// 这对「问一个语言用法」够用，但对「这道题的思路我没懂，再展开讲讲」不够
// —— 学生看不到自己刚才问了什么，也没法往回翻。
//
// 现在把问答按顺序累积成一条时间线。刻意**不把历史随请求发给模型**：
// 档位是教学约束，服务端每次独立裁决；把上一轮的模型回复再喂回去，等于
// 让 AI 自己给自己加码（三档本来是"只给提示"，上一轮若漏出代码被降级，
// 这一轮它会看到降级前的原句）。历史是给学生看的，不是给模型的上下文。

/** 时间线里的一条。`role` 决定左对齐还是右对齐。 */
interface ChatTurn {
  role: 'student' | 'assistant'
  /** 纯文本。AI 的代码块在渲染层按 ``` 切分。 */
  text: string
  /** 被服务端降级过（三档回复像代码）。 */
  downgraded?: boolean
  /** 被拒时的解释 —— 与成功回复一样是普通消息，不是红色报错。 */
  refused?: boolean
}

const turns = ref<ChatTurn[]>([])

/** 滚动容器。新增消息后要跟着滚到底部。 */
const timelineEl = ref<HTMLElement | null>(null)

async function scrollToBottom(): Promise<void> {
  // 等一帧：DOM 还没插入时 scrollHeight 是旧值，直接滚会差一条消息的高度。
  await nextTick()
  const el = timelineEl.value
  if (el) el.scrollTop = el.scrollHeight
}

/** 切换题目 / 作业就清空 —— 上一题的对话接着显示会误导。 */
watch(
  () => [props.assignmentId, props.problemId],
  () => {
    reply.value = null
    failure.value = ''
    question.value = ''
    instruction.value = ''
    turns.value = []
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

/**
 * 提问。
 *
 * 与另外三个动作的区别：它**进时间线**。补全、写一段、整解是一次性的
 * 工具调用（结果要么插进编辑器、要么看完就关），只有对话是来回的。
 *
 * 输入框的处理有个来回：先清空（这样问题立刻出现在右侧，不等网络），
 * 失败时再**填回去**。清空是为了即时反馈，填回是为了重试时不必重打
 * —— 这两件事都成立，所以两件都做。
 */
async function askChat(): Promise<void> {
  const q = question.value.trim()
  if (q === '') return

  turns.value.push({ role: 'student', text: q })
  question.value = ''
  await scrollToBottom()

  await run(() => requestChat({ ...common(), question: q, statement: props.statement }))

  const r = reply.value
  if (failure.value !== '') {
    // 网络故障也要留痕，否则时间线里只剩学生的问题，像被无视了。
    turns.value.push({ role: 'assistant', text: failure.value, refused: true })
    // 把问题放回输入框：故障不是学生的问题，不该让他重打一遍。
    question.value = q
  } else if (r) {
    turns.value.push({
      role: 'assistant',
      text: r.content || r.reason,
      downgraded: r.downgraded,
      refused: !r.allowed
    })
  }
  await scrollToBottom()
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

// --- 模型选择（PU-13）-------------------------------------------------

const models = ref<ModelOption[]>([])
const activeModel = ref('')
/** `.env` 里的默认值，用来在下拉里标一个「默认」。 */
const defaultModel = ref('')
const modelMenuOpen = ref(false)
const modelError = ref('')

/**
 * 拉取可用模型。
 *
 * 失败**不弹错**：AI 面板的主体是提问，模型选单是附属。拉不到就让下拉
 * 不渲染（`v-if="models.length > 1"`），学生照常提问 —— 用的是服务端
 * 的默认模型，与没有这个功能时完全一样。把附属功能的失败变成一条红色
 * 横幅，会让学生以为整个 AI 都不能用了。
 */
async function loadModels(): Promise<void> {
  try {
    const snap = await listModels()
    models.value = snap.models
    activeModel.value = snap.active
    defaultModel.value = snap.default
    modelError.value = ''
  } catch {
    models.value = []
    modelError.value = ''
  }
}

async function chooseModel(id: string): Promise<void> {
  modelMenuOpen.value = false
  if (id === activeModel.value) return
  modelError.value = ''
  try {
    const snap = await setModel(id)
    activeModel.value = snap.active
    models.value = snap.models
    // 换模型后旧回答是新模型给的还是旧的？说不清，所以提示一句。
    turns.value.push({
      role: 'assistant',
      text: `已切换到 ${snap.models.find((m) => m.id === snap.active)?.label ?? snap.active}，接下来的回答由它给出。`,
      refused: false
    })
    await scrollToBottom()
  } catch (err) {
    modelError.value = err instanceof Error ? err.message : '切换失败'
  }
}

const activeLabel = computed<string>(() => {
  const m = models.value.find((x) => x.id === activeModel.value)
  return m?.label ?? activeModel.value
})

onMounted(loadModels)

/**
 * 点面板外部关掉模型菜单。
 *
 * 用 `pointerdown` 而不是 `click`：click 在菜单项自己的 handler 之后才
 * 冒泡到 document，会导致"点选项 → 菜单先关 → 选项的 handler 因为元素
 * 已卸载而没跑到"。pointerdown 在 mousedown 之前、且早于选项的 click。
 */
function onDocPointerDown(e: PointerEvent): void {
  if (!modelMenuOpen.value) return
  const target = e.target as HTMLElement | null
  if (target?.closest('.assist-model')) return
  modelMenuOpen.value = false
}

onMounted(() => document.addEventListener('pointerdown', onDocPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocPointerDown))
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

      <!--
        模型选择（PU-13）。
        只在**真的有得选**时渲染：机器上只配了一个 provider 时，一个只有
        一个选项的下拉是纯噪声。`models.length > 1` 就是"有得选"。
      -->
      <div v-if="models.length > 1" class="assist-model">
        <button
          class="assist-model-trigger"
          type="button"
          :aria-expanded="modelMenuOpen ? 'true' : 'false'"
          title="切换回答用的 AI 模型"
          @click="modelMenuOpen = !modelMenuOpen"
        >
          <span class="assist-model-dot" aria-hidden="true"></span>
          <span class="assist-model-name">{{ activeLabel }}</span>
          <span class="assist-model-caret" aria-hidden="true">▾</span>
        </button>

        <ul v-if="modelMenuOpen" class="assist-model-menu">
          <li v-for="m in models" :key="m.id">
            <button
              class="assist-model-item"
              :class="{ 'is-active': m.id === activeModel }"
              type="button"
              :title="m.base_url"
              @click="chooseModel(m.id)"
            >
              <span class="assist-model-item-name">
                {{ m.label }}
                <span v-if="m.local" class="assist-model-badge">本机</span>
                <span v-else-if="m.id === defaultModel" class="assist-model-badge">默认</span>
              </span>
              <span class="assist-model-item-sub">{{ m.model }}</span>
            </button>
          </li>
        </ul>
      </div>
      <p v-if="modelError" class="assist-note assist-note-error">{{ modelError }}</p>

      <!--
        对话时间线。有来有回，所以按顺序累积；补全/整解那种一次性的产物
        留在下面的 `reply` 区，不进来。
      -->
      <div
        v-if="policy?.allows_chat"
        ref="timelineEl"
        class="assist-timeline"
        role="log"
        aria-live="polite"
      >
        <p v-if="turns.length === 0" class="assist-timeline-empty">
          还没有对话。把不懂的地方问出来 —— 问得越具体，回答越有用。
        </p>

        <div
          v-for="(turn, i) in turns"
          :key="i"
          class="assist-turn"
          :class="[`is-${turn.role}`, { 'is-refused': turn.refused }]"
        >
          <div class="assist-bubble">{{ turn.text }}</div>
          <p v-if="turn.downgraded" class="assist-turn-flag">这一档只给提示，回复已自动降级。</p>
        </div>

        <div v-if="pending" class="assist-turn is-assistant">
          <div class="assist-bubble assist-bubble-thinking">
            <span class="assist-dots" aria-hidden="true"> <i></i><i></i><i></i> </span>
            <span class="assist-thinking-text">正在思考…</span>
          </div>
        </div>
      </div>

      <!-- 对话输入。三档起。三档只给提示，四档起可以写。 -->
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

      <p v-if="failure" class="assist-note assist-note-error">{{ failure }}</p>

      <!--
        一次性动作的产物（补全候选 / 写一段 / 整解）。
        这些不进时间线：它们的目标是编辑器，不是"聊下去"。
      -->
      <div v-if="reply && reply.action !== 'chat'" class="assist-reply">
        <p v-if="!reply.allowed" class="assist-reply-reason">{{ reply.reason }}</p>

        <template v-else>
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
  /*
    这里**不滚**（PU-13 之前是 overflow-y: auto）。
    对话时间线要自己滚，输入框才会停在底部 —— 让整个 body 滚的话，
    长对话会把输入框推到看不见的地方，学生每问一句都要先往下滚。
    由 `.assist-timeline` 承担滚动。
  */
  overflow: hidden;
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

/* --- 模型选择（PU-13）------------------------------------------------- */

.assist-model {
  position: relative;
}

.assist-model-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 8px;
  font-size: 11.5px;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    border-color 0.15s,
    color 0.15s;
}

.assist-model-trigger:hover {
  color: var(--af-text-primary);
  border-color: var(--af-border-strong);
}

/* 一个色点代替图标：本机模型绿、云端的蓝，比文字更省横向空间。 */
.assist-model-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--af-accent);
  box-shadow: 0 0 6px rgb(79 140 255 / 0.6);
}

.assist-model-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.assist-model-caret {
  flex-shrink: 0;
  font-size: 9px;
  color: var(--af-text-muted);
}

.assist-model-menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: var(--af-glass-elevated, var(--af-elevated));
  backdrop-filter: var(--af-glass-blur, none);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  box-shadow: var(--af-shadow-md, 0 8px 24px rgb(0 0 0 / 0.35));
}

.assist-model-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 100%;
  padding: 6px 8px;
  font-size: 11.5px;
  color: var(--af-text-secondary);
  background: none;
  border: none;
  border-radius: 4px;
  text-align: left;
  cursor: pointer;
}

.assist-model-item:hover {
  color: var(--af-text-primary);
  background: var(--af-border);
}

.assist-model-item.is-active {
  color: var(--af-accent);
  background: var(--af-accent-subtle);
}

.assist-model-item-name {
  display: flex;
  align-items: center;
  gap: 5px;
}

.assist-model-item-sub {
  font-size: 10px;
  color: var(--af-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assist-model-badge {
  padding: 0 5px;
  font-size: 9.5px;
  line-height: 1.5;
  color: var(--af-text-muted);
  background: var(--af-canvas);
  border-radius: 999px;
}

/* --- 对话时间线（PU-13）---------------------------------------------- */

.assist-timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-height: 80px;
  overflow-y: auto;
  padding: 2px;
  border-radius: var(--af-radius-sm);
  background: var(--af-canvas);
}

.assist-timeline-empty {
  margin: auto;
  padding: 12px 10px;
  font-size: 11px;
  line-height: 1.6;
  text-align: center;
  color: var(--af-text-muted);
}

.assist-turn {
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-width: 92%;
}

/* 学生的问题靠右、AI 的回答靠左 —— 一眼能分清谁说的。 */
.assist-turn.is-student {
  align-self: flex-end;
  align-items: flex-end;
}

.assist-turn.is-assistant {
  align-self: flex-start;
  align-items: flex-start;
}

.assist-bubble {
  padding: 7px 10px;
  font-size: 12px;
  line-height: 1.7;
  /*
    中文长句在窄栏里必须能断行：`break-word` 不够，英文标识符
    （`priority_queue`）不会在无空格处断开，会撑破 260px 的栏宽。
  */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border-radius: 10px;
}

.assist-turn.is-student .assist-bubble {
  color: #fff;
  background: var(--af-accent);
  border-bottom-right-radius: 3px;
}

.assist-turn.is-assistant .assist-bubble {
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-bottom-left-radius: 3px;
}

/*
  被拒 / 失败用中性色而不是红色：档位是教学设置，不是故障 ——
  与组件 docstring 里"被拒不是错误"的口径一致。
*/
.assist-turn.is-refused .assist-bubble {
  color: var(--af-text-muted);
  font-style: italic;
  background: transparent;
  border-style: dashed;
}

.assist-turn-flag {
  margin: 0;
  font-size: 10.5px;
  color: var(--af-text-muted);
}

.assist-bubble-thinking {
  display: flex;
  align-items: center;
  gap: 8px;
}

.assist-thinking-text {
  font-size: 11px;
  color: var(--af-text-muted);
}

.assist-dots {
  display: inline-flex;
  gap: 3px;
}

.assist-dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--af-accent);
  animation: assist-blink 1.2s infinite ease-in-out;
}

.assist-dots i:nth-child(2) {
  animation-delay: 0.18s;
}

.assist-dots i:nth-child(3) {
  animation-delay: 0.36s;
}

@keyframes assist-blink {
  0%,
  60%,
  100% {
    opacity: 0.25;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .assist-dots i {
    animation: none;
    opacity: 0.6;
  }
}

.assist-chat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex-shrink: 0;
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

.assist-reply-reason {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-secondary);
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
