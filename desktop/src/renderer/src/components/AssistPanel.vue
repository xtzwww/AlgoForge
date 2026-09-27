<script setup lang="ts">
/**
 * 辅助面板 —— 编辑器旁边的 AI（阶段 5）。
 *
 * 这一版重写了三件事
 * ==================
 *
 * 1. **只有一个输入框。** 上一版把「问 AI」和「让 AI 写一段」渲染成两个
 *    长得一模一样的多行输入框，中间只隔一个标签。学生看到的是**两个对话
 *    窗口**，不知道该往哪个里打字 —— 而且两个框都叫"让 AI 做点什么"，
 *    这个困惑是设计造成的，不是学生没看清。现在只留一个输入区，它**该走
 *    对话还是代写由档位决定**（见 `submit`），学生不需要知道这个区别。
 *
 * 2. **学生能自己调辅助强度。** 上一版只显示「5 档 · AI 代做」，学生完全
 *    没有入口去调低它 —— `AssistLevelPicker` / `AssistLevelAxis` 当时只接在
 *    老师端。而服务端
 *    一直是支持学生调低的（`PUT /assistance/level`，夹在 `[1, 老师上限]`），
 *    所以缺的是入口而不是能力。现在把选择器接进来，学生可以自愿用得更少。
 *
 * 3. **五档真的给代码，而且直接填进去。** 上一版五档的「完整解法」把模型
 *    回复原样塞进一个 `<pre>`，里面还带着 ```` ```cpp ```` 围栏，学生要自己
 *    点「插入到编辑器」还不够 —— 粘进去的围栏会让编译器报错。现在五档的
 *    整解会**剥掉围栏、直接替换编辑器内容**，因为"要一份完整答案"这个请求
 *    本身就意味着"我要的就是这份代码"，再让他手点一次是多余的一步。
 *
 * 四件事按档位出现
 * ----------------
 * | 档 | 补全 | 对话 | 写代码 | 整解 |
 * |----|------|------|--------|------|
 * | 1  | 本机 |  —   |   —    |  —   |
 * | 2  | 联网 |  —   |   —    |  —   |
 * | 3  | 联网 | 提示 |   —    |  —   |
 * | 4  | 联网 | 可写 |  可插  |  —   |
 * | 5  | 联网 | 可写 |  可插  | 可填 |
 *
 * **按钮的隐藏只是体验，规则在服务端。** 这里按 `policy` 决定显示什么，
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
 * 代码的去向
 * ----------
 * 面板自己拿不到编辑器，只能发 `insert` / `replace` 事件交给页面。四档的
 * "写一段"发 `insert`（追加，不覆盖学生自己写的东西）；五档的整解发
 * `replace`（替换），因为整解的意义就是"这份代码就是答案"。
 */

import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { NModal } from 'naive-ui'
import {
  chat as requestChat,
  complete as requestComplete,
  getAssistanceConfig,
  getLevelState,
  insert as requestInsert,
  listLevels,
  listModels,
  saveAssistanceConfig,
  setLevelPreference,
  solution as requestSolution,
  testAssistanceConfig,
  type AssistancePolicy,
  type AssistResponse,
  type LevelState,
  type ModelOption
} from '../api/assistance'
import AssistLevelAxis from './AssistLevelAxis.vue'
import { looksLikeCodeRequest } from '../utils/intent'
import { shortModelName } from '../utils/model-name'

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
  /** 把一段代码追加到编辑器末尾。四档的"写一段"走这条。 */
  (e: 'insert', code: string): void
  /** 用一段代码**替换**编辑器全部内容。五档的整解走这条。 */
  (e: 'replace', code: string): void
}>()

/**
 * 不把未声明的属性透传到根元素。
 *
 * 起因是一个真实的警告：父组件传 `:prefix="code"`，而 `prefix` 虽然是本组件
 * 声明的 prop，Vue 仍会把未被 `props` 消费的名字继续当作属性渲染到根
 * `<aside>` 上 —— 于是 `patchDOMProp` 试图写一个**只读的原生属性**
 * `prefix`，每次挂载都抛
 * `TypeError: Cannot set property prefix of [object Element] which has only a getter`。
 *
 * 关掉透传比"把 prop 改个名"更合适：这个面板的根元素是固定的 `<aside>`，
 * 本来就不需要接收外部塞进来的 class/style/属性；将来谁再加一个与 DOM
 * 保留名同名的 prop（`title`、`slot`、`is`…）也不会再踩同一个坑。
 */
defineOptions({ inheritAttrs: false })

/** 面板收起时只剩标题栏，给编辑器让出宽度。 */
const collapsed = ref(false)

/**
 * 两个弹层的开合（本轮新增）。
 *
 * **声明必须在最前面。** 换题的那个 watcher 带 `immediate: true`，会在
 * `setup` 期间立刻跑一次，里面要把它俩复位 —— 如果它们声明在 watcher
 * 之后，`const` 的暂时性死区会抛 `Cannot access 'x' before initialization`，
 * 表现为"所有用例全红"，看起来像整个组件坏了。
 *
 * 为什么收进弹层：上一版刻度尺是**常驻**在面板里的，五格横向铺开每格
 * 50 多像素，档位名（"AI 代做"）只能压到 10px 才放得下 —— 那是"挤成一团"
 * 最直接的一个来源。收进弹层后，面板里只剩「强度 · 5 档」这一个按钮。
 */
const levelOpen = ref(false)
const modelMenuOpen = ref(false)

const instruction = ref('')
const pending = ref(false)
/** 最后一次一次性回复（补全 / 写一段 / 整解）。 */
const reply = ref<AssistResponse | null>(null)
const failure = ref('')

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
  /** 带代码的回复：附一个"填进编辑器"的动作。 */
  code?: string
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

// --- 当前生效档位（2026-09-26）---------------------------------------
//
// 面板过去只拿得到**默认档**（三档），因为它没有渠道知道"这份作业是几档"
// —— 服务端按 `assignment_id` 查，而那个请求是学生每次提问时才发出的。
// 于是老师设了五档、学生的界面仍然画成三档的样子，看起来像"设置没生效"。
//
// 现在进来就问一次 `GET /assistance/level`，拿回三个数：生效档位、老师的
// 上限、学生自己的意愿。界面按**生效档位**渲染，所以它和真正裁决用的一定
// 是同一个数。
//
// 失败时不弹错、也不假装是三档：`levelState` 留空，面板退回父组件传进来的
// `policy`（那是发布时的静态默认），学生照常能用 —— 与没有这个功能时一致。
// 把一次附属请求的失败变成红色横幅，会让学生以为整个 AI 都坏了。

/** 生效档位与它的两个来源。`null` = 还没取到或取失败。 */
const levelState = ref<LevelState | null>(null)

/**
 * 面板据以渲染的规则。
 *
 * **优先生效档位**（`levelState.policy`，来自服务端按 `assignment_id` 的裁决），
 * 取不到时退回父组件传的 `policy`（发布时的静态默认）。
 */
const policy = computed<AssistancePolicy | null>(() => levelState.value?.policy ?? props.policy)

const level = computed<number>(() => policy.value?.level ?? 0)
const ready = computed<boolean>(() => policy.value !== null)
const disabled = computed<boolean>(() => props.busy === true || pending.value)
const levelError = ref('')

/**
 * 上一次调整档位的结果（成功）。
 *
 * **不能塞进对话时间线。** 时间线整块挂在 `policy.allows_chat` 下面，而
 * 往低档调正是最需要学生看到确认的时刻 —— 二档没有对话区，那条"已调成
 * 2 档"会随着时间线一起消失，学生看到的是自己点了、界面只变了顶栏，
 * 像是没保存成功。所以它有自己的位置，挂在刻度尺边上。
 *
 * 有失败（`levelError`）时这条不显示：一件事只用一个位置说一次。
 */
const levelNotice = ref('')

async function loadLevelState(): Promise<void> {
  try {
    levelState.value = await getLevelState(props.assignmentId)
    levelError.value = ''
  } catch {
    // 静默降级，见上面注释。不改 `levelState`，保留上一次成功的值。
    levelError.value = ''
  }
}

// --- 学生自己调辅助强度（本轮新增）------------------------------------
//
// 服务端一直支持学生把档位往**更严格**的方向调（`PUT /assistance/level`，
// 值会被夹进 `[1, 老师的上限]`）。缺的只是界面入口 —— 上一版面板只有一句
// 「5 档 · AI 代做」，学生除了接受它没有任何操作空间。
//
// 为什么学生需要这个：老师设的是"这次作业允许的上限"，不是"每个人都该顶格
// 用"。复习时想要五档、模拟考时想要一档，是两种诚实的学习方式。压住后者的
// 产品会逼他关掉页面去别处找 AI。
//
// 学生端的选择器是 `AssistLevelAxis`（横向刻度轴，收在弹层里），并把
// `ceiling` 传成老师的上限，于是学生看得到"档位一共五级、我最多能用到第
// 几级"，而不是一个光秃秃的下拉框。`allowDefault` 打开，因为"不指定"
// （跟着老师走）本身是一种选择。
//
// 老师端用的是另一个组件 `AssistLevelPicker`（横向刻度尺）：他有整页宽度，
// 而且需要一眼看到"一共五档"，横向铺开最直观。两个组件共用的只有
// `levels` / `ceiling` / `allowDefault` 这几个 prop 的语义。

/** 五档规则。与 `levelState` 分开取：规则是静态的，档位状态是每次变的。 */
const levels = ref<AssistancePolicy[]>([])
/** 正在保存选择。选择器据此禁用，避免连点写乱。 */
const savingPreference = ref(false)

/**
 * 选择器绑定的值。
 *
 * 这是"学生当前想要的那一档"：没表过态时是老师的上限，表过态时是他自己
 * 选的那个。用可写 computed 而不是 ref，是因为它有两个来源（服务端状态、
 * 学生点击），而两者必须始终一致 —— 各存一份必然会漂移。
 */
const pickerLevel = computed<number | null>({
  get(): number | null {
    const s = levelState.value
    if (!s) return null
    // 没表过态时显示成"使用默认"，而不是显式选中上限那一档：两者在
    // 服务端是不同的状态（老师之后改档位时行为不同），界面也该说清。
    return s.preference
  },
  set(value: number | null) {
    void savePreference(value)
  }
})

/** 老师的上限。选择器用它画分界线并把更高的档禁用。 */
const ceiling = computed<number | null>(() => levelState.value?.ceiling ?? null)

/**
 * 保存学生的选择。
 *
 * 传 `null` 而不是老师的档位数字：语义是"清除我的意愿"，不是"我要老师
 * 那一档"。区别在老师之后改了档位时体现 —— 传数字会把旧值钉死，传 null
 * 会跟着老师走。见 `api/assistance.ts` 的 `setLevelPreference`。
 *
 * 保存失败时**不把选择器回滚成乐观值**：服务端返回的 `levelState` 才是
 * 真相，失败时它没变，可写 computed 自然读回旧值 —— 界面于是自动纠正。
 */
async function savePreference(value: number | null): Promise<void> {
  if (savingPreference.value) return
  if (value === levelState.value?.preference) return
  savingPreference.value = true
  levelError.value = ''
  levelNotice.value = ''
  try {
    levelState.value = await setLevelPreference(value, props.assignmentId)
    levelNotice.value =
      value === null
        ? `已改回老师设置的辅助强度（${levelState.value.ceiling} 档）。`
        : `已把这次作业的辅助强度调成 ${value} 档，之后按这个执行。`
    // 选完就收起弹层：确认信息写在工具条下方，弹层不收会把它盖住；
    // 而且学生在滚轮里再滚一下就又改了，收起是"这件事结束了"的信号。
    levelOpen.value = false
  } catch (err) {
    levelError.value = err instanceof Error ? err.message : '无法保存你的选择'
  } finally {
    savingPreference.value = false
  }
}

/** 切换题目 / 作业就清空 —— 上一题的对话接着显示会误导。 */
watch(
  () => [props.assignmentId, props.problemId],
  () => {
    reply.value = null
    failure.value = ''
    instruction.value = ''
    turns.value = []
    // 弹层也要收：留着的话，切到新题时那个滚轮里显示的仍是上一题的档位，
    // 而它下面已经换成了新题的内容。
    levelOpen.value = false
    modelMenuOpen.value = false
    levelNotice.value = ''
    // 档位是**作业**的属性，换一份就是另一个数，必须重取。
    // 合进这一条而不是另开一个 watcher：两个 watcher 盯同一个来源，
    // 触发顺序依赖注册顺序，将来插一条就会静默改变行为。
    void loadLevelState()
  },
  { immediate: true }
)

function common(): {
  assignment_id?: number
  problem_id?: number
  language?: string
  provider?: string
} {
  return {
    // 只有真的绑在作业上才传。传 null 会被服务端当成"没有作业"，与省略等价，
    // 但显式传 null 会让 pydantic 收到一个 None 而不是缺省值 —— 少一层歧义。
    ...(props.assignmentId === null ? {} : { assignment_id: props.assignmentId }),
    ...(props.problemId === null ? {} : { problem_id: props.problemId }),
    language: props.language,
    // 选了模型就随请求带上，逐请求生效、只影响自己。不再走 setModel 改
    // 服务端的进程级默认值（那需要教师身份，且会牵动所有人）—— 见
    // api/assistance.ts 里 setModel 与 AssistRequest.provider 的注释。
    ...(activeModel.value ? { provider: activeModel.value } : {})
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
 * 五档的整解：剥掉 Markdown 围栏、取出可编译的代码。
 *
 * 逻辑在 `utils/code-extract.ts` —— 纯字符串函数，单独可测，也与 Vue 无关。
 */
import { extractCode, looksLikeProgram } from '../utils/code-extract'

/** 剥掉模型回复外面的 Markdown 代码围栏。 */

/**
 * 统一输入区的提交。
 *
 * **这是"只留一个输入框"的落点（2026-09-27 合并）。** 上一版在四、五档
 * 时输入框上方多了一组「写代码 / 问问题」二选一按钮 —— 学生看到的是
 * "两个窗口"，每打一句话之前都要先决定走哪边。现在按钮整个去掉，学生
 * 只面对一个输入框，面板按档位与句子意图自动路由：
 *
 * - 档位同时允许对话与代写（四、五档）时，按**句子意图**判断：像
 *   "写一段…"（判据在 `utils/intent.ts`，只认生成动词，刻意偏保守）走
 *   代写，其余走对话。宁可把"要代码的话"送进对话（对话本身也能给代码），
 *   也不要把"问思路的话"送进代写（会真的把一段代码插进编辑器）。
 * - 只允许代写 → 代写。
 * - 只允许对话（三档）→ 对话。三档的对话只给提示，这是服务端的规则。
 * - 连对话都不允许（一、二档）→ 什么都不发生，输入框本来就不该显示。
 *
 * 五档的整解是另一条：整解是一条**独立的一次性动作**（"给我这道题的
 * 答案"），它需要题干而不是学生这句话，所以留在动作区，不进这里。
 */

/**
 * 底部工具条是否出现。
 *
 * 一档、二档两个能力都没有（只有本机/AI 补全），输入框对它们是无效的
 * —— 学生打了字也没有任何一条路可走。那时工具条整个不渲染，面板就只剩
 * 顶栏 + 对话区 + 那条"这一档只提供补全"的说明。
 */
const hasComposer = computed<boolean>(
  () => (policy.value?.allows_chat ?? false) || (policy.value?.allows_code_insertion ?? false)
)

async function submit(): Promise<void> {
  const text = instruction.value.trim()
  if (text === '') return
  const policyNow = policy.value
  if (!policyNow) return

  const both = policyNow.allows_chat && policyNow.allows_code_insertion
  if (both) {
    if (looksLikeCodeRequest(text)) {
      await submitAsInsert(text)
      return
    }
    await submitQuestion(text)
    return
  }

  if (policyNow.allows_code_insertion) {
    await submitAsInsert(text)
    return
  }

  if (policyNow.allows_chat) {
    await submitQuestion(text)
    return
  }
}

/**
 * 代写线（四档起）。结果落在一次性产出区，附一个「插入到编辑器末尾」。
 *
 * 也进时间线留痕，否则学生看不到自己刚要求了什么。
 */
async function submitAsInsert(text: string): Promise<void> {
  turns.value.push({ role: 'student', text })
  instruction.value = ''
  await scrollToBottom()

  await run(() =>
    requestInsert({ ...common(), instruction: text, current_code: props.currentCode })
  )
  recordAssistantTurn()
}

/**
 * 提问（对话线）。与 `submit` 分开是因为它也能被"回车"单独触发，
 * 且它的失败处理多一步：把问题填回输入框。
 */
async function submitQuestion(text: string): Promise<void> {
  turns.value.push({ role: 'student', text })
  instruction.value = ''
  await scrollToBottom()

  await run(() => requestChat({ ...common(), question: text, statement: props.statement }))

  if (failure.value !== '') {
    // 网络故障也要留痕，否则时间线里只剩学生的问题，像被无视了。
    turns.value.push({ role: 'assistant', text: failure.value, refused: true })
    // 把问题放回输入框：故障不是学生的问题，不该让他重打一遍。
    instruction.value = text
  }
  recordAssistantTurn()
}

/**
 * 把 `reply` 追加进时间线。
 *
 * 抽出来是因为代写与对话两条线都要做同一件事，且**判据必须一致**：
 * 被拒时 `content` 为空、`reason` 非空，两处若各写一遍索引，早晚会有一处
 * 显示空白气泡。
 */
function recordAssistantTurn(): void {
  const r = reply.value
  if (failure.value !== '' || !r) return
  const body = r.content || r.reason
  turns.value.push({
    role: 'assistant',
    text: body,
    downgraded: r.downgraded,
    refused: !r.allowed,
    /*
      时间线里出现完整代码时给一个"替换编辑器"入口，但**只有五档才给**。
      四档的回复也会带代码（那是"写一段"的产物，走 insert），而四档的
      定义是"帮你写一部分，剩下的自己想" —— 给它一个一键替换整份代码的
      按钮，等于把四档偷偷升成五档，服务端那层的克制就白做了。四档的产物
      走产出区的「插入到编辑器末尾」，那条路只追加、不覆盖。
    */
    code:
      r.allowed && (r.policy?.allows_full_solution ?? false) && looksLikeProgram(body)
        ? extractCode(body)
        : undefined
  })
}

/** 补全。一档是本机候选词，二档起是模型给的一段代码。 */
async function askCompletion(): Promise<void> {
  await run(() => requestComplete({ ...common(), prefix: props.prefix }))
}

/**
 * 五档的完整解法。
 *
 * **生成完直接替换编辑器内容**，不再让学生点一次「插入到编辑器」——
 * "给我完整解法"这个请求本身就意味着"我要这份代码"，把最后一步留给他
 * 是多余的动作，而这一步又最容易出错（上一版粘进去的还带着围栏）。
 *
 * 仍然**不静默覆盖**：替换发的是独立事件，由页面决定要不要确认。面板
 * 拿不到编辑器，也就不可能绕过页面的替换语义。
 *
 * 生成失败或不是代码（模型答了一句话）时不动编辑器，只把内容显示出来。
 */
async function askSolution(): Promise<void> {
  await run(() => requestSolution({ ...common(), statement: props.statement }))
  const r = reply.value
  if (!r || !r.allowed || r.content === '') return
  const code = extractCode(r.content)
  if (!looksLikeProgram(code)) return
  emit('replace', code)
}

/** 把代码追加进编辑器。四档的"写一段"与候选词点击走这条。 */
function useCode(code: string): void {
  if (code.trim() === '') return
  emit('insert', code)
}

/** 用一段代码替换编辑器内容。整解与对话里出现的完整代码走这条。 */
function replaceCode(code: string): void {
  if (code.trim() === '') return
  emit('replace', code)
}

// --- 模型选择（PU-13）-------------------------------------------------

const models = ref<ModelOption[]>([])
const activeModel = ref('')
/** `.env` 里的默认值，用来在下拉里标一个「默认」。 */
const defaultModel = ref('')
const modelError = ref('')

/** 两个弹层互斥：同时开着会互相盖住，而且它们都是"当前设定"，不该并存。 */
function toggleLevel(): void {
  levelOpen.value = !levelOpen.value
  if (levelOpen.value) modelMenuOpen.value = false
}

function toggleModel(): void {
  modelMenuOpen.value = !modelMenuOpen.value
  if (modelMenuOpen.value) levelOpen.value = false
}

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

/**
 * 选模型：只改本地状态，不调服务端。
 *
 * 选中的 id 通过 `common()` 里的 `provider` 字段随请求发出去，因此只影响
 * 自己接下来的提问 —— 不需要任何权限，也不会牵动别人。这与修改服务端进程
 * 默认（`setModel`，教师专属）是两条路。
 */
async function chooseModel(id: string): Promise<void> {
  modelMenuOpen.value = false
  levelOpen.value = false
  if (id === activeModel.value) return
  modelError.value = ''
  const label = models.value.find((m) => m.id === id)?.label ?? id
  activeModel.value = id
  // 旧回答是哪个模型给的、新回答是哪个给的，说不清，提示一句即可。
  turns.value.push({
    role: 'assistant',
    text: `已切换到 ${label}，接下来的回答由它给出。`,
    refused: false
  })
  await scrollToBottom()
}

// --- 配置自定义 API 端点（2026-09-27）-----------------------------------
//
// 面板只列得出**这台机器上配好了的**模型（`app/model_catalog` 只收录
// 配置齐全的 provider）。学生/老师想用自己的端点（DeepSeek、vLLM、
// LM Studio、中转网关…）时，这里提供一个引导窗口：填 Base URL / Key /
// 模型名 → 先试连（不落盘）→ 保存写进 sidecar/.env → 重启后端生效。
//
// 为什么是窗口而不是直接改代码：写 .env 是**写配置文件**，不是发请求，
// 这个动作值得一眼看清（"写入路径"明示在窗口里）。试连和保存分开，是
// 为了让学生先验证再落盘 —— 一个填错的 URL 不该先写进配置再等重启报错。

const configOpen = ref(false)
const cfgForm = reactive({ base_url: '', api_key: '', model: '' })
const cfgBusy = ref(false)
const cfgStatus = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
const cfgEnvPath = ref('')
/** 刚保存过 —— 当前进程仍在用旧配置，需要重启后端。 */
const cfgRestartNote = ref(false)

/** 打开窗口：预填当前生效配置。密钥只回传脱敏值，Key 框留空 = 保持原值。 */
async function openConfig(): Promise<void> {
  modelMenuOpen.value = false
  levelOpen.value = false
  cfgStatus.value = null
  cfgRestartNote.value = false
  cfgForm.api_key = ''
  try {
    const cur = await getAssistanceConfig()
    cfgForm.base_url = cur.base_url
    cfgForm.model = cur.model
    cfgEnvPath.value = cur.env_path
  } catch {
    // 读不到就开空表单：窗口本身不依赖预填才能用。
    cfgForm.base_url = ''
    cfgForm.model = ''
    cfgEnvPath.value = ''
  }
  configOpen.value = true
}

async function runConfigTest(): Promise<void> {
  const url = cfgForm.base_url.trim()
  const model = cfgForm.model.trim()
  if (!url || !model) {
    cfgStatus.value = { kind: 'err', text: '先填 Base URL 和模型名，再测试连接' }
    return
  }
  cfgBusy.value = true
  cfgStatus.value = null
  try {
    const r = await testAssistanceConfig({
      base_url: url,
      api_key: cfgForm.api_key.trim(),
      model
    })
    cfgStatus.value = { kind: r.ok ? 'ok' : 'err', text: r.message }
  } catch (err) {
    cfgStatus.value = {
      kind: 'err',
      text: err instanceof Error ? err.message : '无法连接，请检查地址与网络'
    }
  } finally {
    cfgBusy.value = false
  }
}

async function saveConfig(): Promise<void> {
  const url = cfgForm.base_url.trim()
  const model = cfgForm.model.trim()
  if (!url || !model) {
    cfgStatus.value = { kind: 'err', text: 'Base URL 和模型名必填' }
    return
  }
  cfgBusy.value = true
  cfgStatus.value = null
  try {
    const saved = await saveAssistanceConfig({
      base_url: url,
      model,
      ...(cfgForm.api_key.trim() ? { api_key: cfgForm.api_key.trim() } : {})
    })
    cfgEnvPath.value = saved.env_path
    cfgRestartNote.value = true
    cfgStatus.value = { kind: 'ok', text: `已写入 ${saved.env_path}，重启后端后生效。` }
    // 刷新模型目录：catalog 快照是按 .env 生成的，重启后模型才会出现，
    // 但此刻刷新至少让界面与快照一致，而不是留着旧列表。
    void loadModels()
  } catch (err) {
    cfgStatus.value = { kind: 'err', text: err instanceof Error ? err.message : '保存失败' }
  } finally {
    cfgBusy.value = false
  }
}



/**
 * 模型按钮上显示的短名（2026-09-27）：`agnes-3.0-flash` → `agnes3.0f`。
 * 完整模型名进弹层里显示，按钮上放不下。
 */
const activeLabel = computed<string>(() => {
  const m = models.value.find((x) => x.id === activeModel.value)
  return shortModelName(m?.model ?? activeModel.value)
})

onMounted(async () => {
  await Promise.all([loadModels(), loadLevels()])
})

/** 五档规则。与档位状态分开取 —— 规则是静态的，取一次就够。 */
async function loadLevels(): Promise<void> {
  try {
    levels.value = await listLevels()
  } catch {
    // 取不到规则就不渲染选择器：一个没有刻度的空选择器比没有更糟。
    levels.value = []
  }
}

/**
 * 点面板外部关掉弹层。
 *
 * 用 `pointerdown` 而不是 `click`：click 在菜单项自己的 handler 之后才
 * 冒泡到 document，会导致"点选项 → 菜单先关 → 选项的 handler 因为元素
 * 已卸载而没跑到"。pointerdown 在 mousedown 之前、且早于选项的 click。
 *
 * 判据用 `.assist-bar` 而不是各自的弹层：两个弹层和两个切换按钮都挂在
 * 工具条里 —— 按各自的弹层判断，点另一个按钮会先被这里关掉再被自己的
 * handler 打开，看起来像"点不动"。
 */
function onDocPointerDown(e: PointerEvent): void {
  if (!modelMenuOpen.value && !levelOpen.value) return
  const target = e.target as HTMLElement | null
  if (target?.closest('.assist-bar')) return
  modelMenuOpen.value = false
  levelOpen.value = false
}

onMounted(() => document.addEventListener('pointerdown', onDocPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocPointerDown))

/** 输入框的占位文字跟随档位：四档以上两种都能做，提示语说清"都行"。 */
const inputPlaceholder = computed<string>(() => {
  if (!policy.value) return '正在读取本次作业允许的辅助强度…'
  if (policy.value.allows_chat && policy.value.allows_code_insertion) {
    return '问思路，或者直接说「写一段代码」'
  }
  if (policy.value.allows_code_insertion) return '例如：写一个读入 n 然后建数组的循环'
  return '例如：这道题为什么用前缀和？'
})

/** 输入框上方的标签。合并后不再区分两种模式，标签只说"能做什么"。 */
const inputLabel = computed<string>(() => {
  if (policy.value?.allows_code_insertion) return '让 AI 帮你写，或问它问题'
  return '问 AI'
})

/** 按钮文字。与标签同源，避免"写着提问、点下去是生成"。 */
const submitLabel = computed<string>(() => {
  if (policy.value?.allows_code_insertion) return '发送'
  return '提问'
})
</script>

<template>
  <aside class="assist-panel" :class="{ collapsed }">
    <!--
      顶栏：收起/展开 + 当前档位。
      档位写在这里而不是正文里，因为收起后它仍然要可见 —— "这次作业允许
      多少 AI"是学生随时想确认的一件事，不该只在展开面板时才知道。
    -->
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
      <!--
        取不到规则时说"读取中"而不是"1 档"：默认到最严看起来像产品决定，
        其实只是请求还没回来。
      -->
      <span v-if="!collapsed && ready" class="assist-level" :title="policy?.description">
        <span class="assist-level-num">{{ level }}</span>
        <span class="assist-level-rest">档 · {{ policy?.student_label }}</span>
      </span>
      <span v-else-if="!collapsed" class="assist-level assist-level-unknown">档位读取中…</span>
    </header>

    <div v-if="!collapsed" class="assist-body">
      <!--
        ── 主体：对话区 ──────────────────────────────────────────────
        面板的绝大部分高度给对话。上一版是"十层控件竖着平铺"，每层视觉
        重量相同，于是看不出哪里是主体、哪里是设置 —— 用户的原话是
        "挤成一团"。现在改成：说明与设置收进顶部的提示条 / 弹层，中间
        只剩对话，底部只剩一条输入工具条。
      -->
      <div ref="timelineEl" class="assist-timeline" role="log" aria-live="polite">
        <p v-if="turns.length === 0" class="assist-timeline-empty">
          <span class="assist-empty-mark" aria-hidden="true">✦</span>
          <span class="assist-empty-title">还没有对话</span>
          <span class="assist-empty-text">
            把不懂的地方问出来 —— 问得越具体，回答越有用。
          </span>
        </p>

        <div
          v-for="(turn, i) in turns"
          :key="i"
          class="assist-turn"
          :class="[`is-${turn.role}`, { 'is-refused': turn.refused }]"
        >
          <div class="assist-bubble">{{ turn.text }}</div>
          <p v-if="turn.downgraded" class="assist-turn-flag">
            这一档只给提示，回复已自动降级。
          </p>
          <!--
            回复里带着可编译的代码时给一个填入入口。不自动填 —— 这是
            对话里顺带出现的代码，学生可能只是想看一眼。
          -->
          <button
            v-if="turn.code"
            class="assist-turn-action"
            type="button"
            @click="replaceCode(turn.code)"
          >
            <span class="assist-turn-action-icon" aria-hidden="true">⤓</span>
            用这段代码替换编辑器
          </button>
        </div>

        <div v-if="pending" class="assist-turn is-assistant">
          <div class="assist-bubble assist-bubble-thinking">
            <span class="assist-dots" aria-hidden="true"> <i></i><i></i><i></i> </span>
            <span class="assist-thinking-text">正在思考…</span>
          </div>
        </div>
      </div>

      <!--
        一次性产出（补全 / 写一段 / 整解）。
        放在对话下方而不是上面：它是"刚发生的事"，顺着时间往下加更自然。
      -->
      <div v-if="reply && reply.action !== 'chat'" class="assist-reply">
        <p v-if="!reply.allowed" class="assist-reply-reason">{{ reply.reason }}</p>

        <template v-else>
          <pre v-if="reply.content" class="assist-code">{{ extractCode(reply.content) }}</pre>

          <ul v-if="candidates.length" class="assist-candidates">
            <li v-for="(item, i) in candidates" :key="i">
              <button class="assist-candidate" type="button" @click="useCode(item)">
                {{ item }}
              </button>
            </li>
          </ul>

          <!-- 能写进缓冲区的东西才给按钮；提示型回复进不了编辑器。 -->
          <button
            v-if="reply.content && reply.action === 'insert'"
            class="btn btn-primary btn-sm"
            type="button"
            @click="useCode(extractCode(reply.content))"
          >
            插入到编辑器末尾
          </button>
          <p
            v-else-if="
              reply.content && reply.action === 'full_solution' && !looksLikeProgram(reply.content)
            "
            class="assist-note"
          >
            回复里没有可识别的代码，已显示在上面。
          </p>

          <p v-if="!hasReply" class="assist-note">{{ reply.reason }}</p>
        </template>
      </div>

      <p v-if="failure" class="assist-note assist-note-error">{{ failure }}</p>

      <!--
        ── 底部工具条 ───────────────────────────────────────────────
        分两段：
          上段（`.assist-composer`）= 输入框 + 档位/模型按钮，
            只在**真的有对话或代写能力**时出现（一、二档打了字也没路可走）。
          下段（`.assist-bar`）= 补全 / 整解 + 档位与模型按钮，
            **一档也要有** —— 一档的本机补全就是这次作业允许的全部帮助，
            把它连工具条一起藏掉，等于对学生说"这道题没有任何 AI"。
        -->
      <div v-if="hasComposer" class="assist-composer">
        <!--
          只有一个输入框（2026-09-27 合并）。四、五档时"问 / 写"由面板按
          句子意图自动路由（见 `submit`），学生不需要先决定走哪边 ——
          上一版的「写代码 / 问问题」两个按钮就是"两个窗口"印象的来源。
        -->
        <div class="assist-compose">
          <textarea
            id="assist-instruction"
            v-model="instruction"
            class="assist-input"
            rows="1"
            :placeholder="inputPlaceholder"
            :disabled="disabled"
            @keydown.ctrl.enter.prevent="submit"
            @keydown.meta.enter.prevent="submit"
          />
          <button
            class="assist-submit"
            type="button"
            :disabled="disabled || instruction.trim() === ''"
            :title="submitLabel"
            @click="submit"
          >
            <!-- 文本单独一层：图标是装饰，不该混进按钮的可读名字里。 -->
            <span class="assist-submit-text">{{ submitLabel }}</span>
            <span class="assist-submit-icon" aria-hidden="true">↵</span>
          </button>
        </div>

        <!-- 标签行：说明这一句会走哪条路，以及是不是只给提示。 -->
        <p class="assist-label-row">
          <span class="assist-label">{{ inputLabel }}</span>
          <span v-if="policy?.chat_is_hint_only" class="assist-tag">只给提示</span>
          <span class="assist-keyhint">Ctrl+Enter 发送</span>
        </p>
      </div>

      <!--
        下段：动作按钮 + 两个弹层按钮。
        补全按钮对**每一档**都存在（一档是本机词表、二档起走模型），所以
        这一段不跟着 `hasComposer` 走。
      -->
      <div class="assist-bar">
        <div class="assist-actions">
          <button
            class="btn btn-secondary btn-sm"
            type="button"
            :disabled="disabled || !ready"
            :title="
              policy?.completion_uses_model ? '由 AI 给出接下来的写法' : '本机词表补全，不联网'
            "
            @click="askCompletion"
          >
            {{ policy?.completion_uses_model ? 'AI 补全' : '本机补全' }}
          </button>

          <button
            v-if="policy?.allows_full_solution"
            class="btn btn-secondary btn-sm"
            type="button"
            :disabled="disabled || statement.trim() === ''"
            title="让 AI 给出这道题的完整解法，并直接填进编辑器"
            @click="askSolution"
          >
            完整解法
          </button>
        </div>

        <!--
          两个弹层按钮并排。它们都是"当前设定"——收在按钮上、点开才展开
          可选值，这样工具条的高度恒定，不会因为多了一个模型而变高。
        -->
        <div class="assist-switches">
          <button
            v-if="levelState && levels.length"
            class="assist-switch"
            type="button"
            :aria-expanded="levelOpen ? 'true' : 'false'"
            title="调整这次作业的 AI 辅助强度"
            @click="toggleLevel"
          >
            <span class="assist-switch-icon" aria-hidden="true">◧</span>
            <span class="assist-switch-label">强度</span>
            <span class="assist-switch-value">{{ level }} 档</span>
            <span class="assist-switch-caret" aria-hidden="true">▾</span>
          </button>

          <button
            v-if="models.length >= 1"
            class="assist-switch assist-switch-model"
            type="button"
            :aria-expanded="modelMenuOpen ? 'true' : 'false'"
            title="切换模型，或配置自己的 API 端点"
            @click="toggleModel"
          >
            <span class="assist-model-dot" aria-hidden="true"></span>
            <span class="assist-switch-label">模型</span>
            <span class="assist-switch-value">{{ activeLabel }}</span>
            <span class="assist-switch-caret" aria-hidden="true">▾</span>
          </button>
        </div>

        <!--
          档位弹层。向上弹（工具条在底部，向下弹会被面板底边切掉）。
        -->
        <div v-if="levelOpen" class="assist-pop assist-pop-level">
          <div class="assist-pop-head">
            <span class="assist-pop-title">我的辅助强度</span>
            <span v-if="savingPreference" class="assist-pop-hint">保存中…</span>
            <button
              class="assist-pop-close"
              type="button"
              title="收起"
              @click="levelOpen = false"
            >
              ✕
            </button>
          </div>
          <AssistLevelAxis
            v-model="pickerLevel"
            :levels="levels"
            :ceiling="ceiling"
            :default-level="ceiling"
            allow-default
            :disabled="savingPreference"
          />
        </div>

        <!--
          模型弹层。理由同上，向上弹。
        -->
        <ul v-if="modelMenuOpen" class="assist-pop assist-pop-model">
          <li v-for="m in models" :key="m.id">
            <button
              class="assist-model-item"
              :class="{ 'is-active': m.id === activeModel }"
              type="button"
              :title="`${m.label} — ${m.model}（${m.base_url}）`"
              @click="chooseModel(m.id)"
            >
              <span class="assist-model-item-name">
                {{ shortModelName(m.model) }}
                <span v-if="m.local" class="assist-model-badge">本机</span>
                <span v-else-if="m.id === defaultModel" class="assist-model-badge">默认</span>
              </span>
              <span class="assist-model-item-sub">{{ m.label }} · {{ m.model }}</span>
            </button>
          </li>
          <li class="assist-model-divider" aria-hidden="true"></li>
          <li>
            <button class="assist-model-item" type="button" @click="openConfig">
              <span class="assist-model-item-name">配置自定义 API 端点…</span>
              <span class="assist-model-item-sub">Base URL / Key / 模型，写入 .env</span>
            </button>
          </li>
        </ul>
      </div>

      <!--
        档位调完后的确认与失败。
        放在工具条**之外**：低档位没有工具条，而"往低调"正是最需要看到
        确认的时刻 —— 挂进工具条的话这条消息会随着工具条一起消失。
      -->
      <p v-if="levelNotice" class="assist-note assist-note-ok">{{ levelNotice }}</p>
      <p v-if="levelError" class="assist-note assist-note-error">{{ levelError }}</p>
      <p v-if="modelError" class="assist-note assist-note-error">{{ modelError }}</p>

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
    </div>
  </aside>

  <!--
    配置自定义 API 端点（2026-09-27）。
    用 NModal 而不是面板内弹层：弹层是"当前设定"的展开（不抢焦点、不开
    始即用），而这是**写配置文件**的动作，需要一个明确的一对一窗口。
    Provider 由 App.vue 根部的 NConfigProvider 提供，主题与全应用一致。
  -->
  <NModal
    v-model:show="configOpen"
    preset="card"
    title="配置 AI 接口"
    :style="{ width: '440px', maxWidth: '92vw' }"
  >
    <div class="assist-config">
      <p class="assist-config-hint">
        指向任意 OpenAI 兼容端点（DeepSeek / vLLM / LM Studio / 中转网关…）。
        配置会写入 <code>{{ cfgEnvPath || 'sidecar/.env' }}</code>，保存后重启后端生效。
      </p>

      <label class="assist-config-field">
        <span class="assist-config-label">Base URL</span>
        <input
          v-model="cfgForm.base_url"
          class="assist-config-input"
          type="url"
          placeholder="https://api.example.com"
          :disabled="cfgBusy"
          spellcheck="false"
        />
      </label>

      <label class="assist-config-field">
        <span class="assist-config-label">API Key</span>
        <input
          v-model="cfgForm.api_key"
          class="assist-config-input"
          type="password"
          placeholder="留空 = 保持原值（自建端点可无鉴权）"
          :disabled="cfgBusy"
          autocomplete="off"
        />
      </label>

      <label class="assist-config-field">
        <span class="assist-config-label">模型名</span>
        <input
          v-model="cfgForm.model"
          class="assist-config-input"
          type="text"
          placeholder="例如 agnes-3.0-flash"
          :disabled="cfgBusy"
          spellcheck="false"
        />
      </label>

      <p v-if="cfgStatus" class="assist-config-status" :class="cfgStatus.kind === 'ok' ? 'is-ok' : 'is-err'">
        {{ cfgStatus.text }}
      </p>
      <p v-if="cfgRestartNote" class="assist-config-note">
        当前进程还在用旧配置。重启后端（或重新启动本应用）后，新端点与模型才会出现在模型列表里。
      </p>
    </div>

    <template #footer>
      <div class="assist-config-actions">
        <button class="btn btn-secondary btn-sm" type="button" :disabled="cfgBusy" @click="runConfigTest">
          测试连接
        </button>
        <button class="btn btn-primary btn-sm" type="button" :disabled="cfgBusy" @click="saveConfig">
          保存
        </button>
        <button class="btn btn-secondary btn-sm" type="button" :disabled="cfgBusy" @click="configOpen = false">
          取消
        </button>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
/*
  面板的整体骨架（2026-09-26 重排）
  =================================
  三段式：顶栏 / 对话区 / 底部工具条。

  上一版是"十层框竖着平铺"：说明、刻度尺、确认消息、两个动作按钮、
  空状态、二选一开关、输入框、生成按钮、模型下拉，每层都是一条边框 + 一段
  文字，视觉重量完全相同。用户的原话是"还是挤成一团"。

  这次的处理原则是**分层**，不是加更多框：
  - 对话区独占剩余高度，是唯一会滚动的东西，也是唯一有背景色的区域；
  - 其余一切（档位、模型、补全、整解）都收进底部工具条或它上面的弹层，
    工具条永远只有固定的几行高；
  - 强度选择器用横向刻度轴（`AssistLevelAxis`，2026-09-27）：五格横向
    铺开，档位名只显示短名，说明文字走轴下方的 summary 行 —— 在 340px
    面板里放得下，且"一档到五档是一根能量条"比纵向滚轮更直观。
*/

.assist-panel {
  display: flex;
  flex-direction: column;
  width: 340px;
  flex-shrink: 0;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  overflow: hidden;
}

.assist-panel.collapsed {
  width: 36px;
}

/* --- 顶栏 ------------------------------------------------------------ */

.assist-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--af-border);
}

.assist-panel.collapsed .assist-head {
  justify-content: center;
  padding: 6px;
  border-bottom: none;
}

.assist-toggle {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 5px;
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-secondary);
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

/* 档位徽标：数字放大、单位缩小。顶栏一眼看到的是"几档"。 */
.assist-level {
  display: flex;
  align-items: baseline;
  gap: 3px;
  margin-left: auto;
  padding: 2px 8px;
  font-size: 10.5px;
  color: var(--af-text-muted);
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: 999px;
  white-space: nowrap;
}

.assist-level-num {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--af-accent);
  font-variant-numeric: tabular-nums;
}

.assist-level-unknown {
  font-style: italic;
}

/* --- 主体 ------------------------------------------------------------ */

.assist-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-height: 0;
  /*
    这里**不滚**。滚动由对话区承担 —— 让整个 body 滚的话，长对话会把
    工具条推出可视区，学生每问一句都要先往下滚。
  */
  overflow: hidden;
  padding: 10px;
}

/* --- 对话区（面板主体）----------------------------------------------- */

.assist-timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
  /*
    `min-height: 0` 是必需的：flex 子项默认 `min-height: auto`，内容一长
    就会把容器顶开、于是 `overflow-y` 永远不生效（面板跟着被撑高，工具条
    被挤出屏幕）。这是"滚不动"最常见的原因。
  */
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  /*
    对话区是面板里唯一有独立底色的区域，靠它与工具栏区分开：深色画布上
    浮着一块"玻璃"，比再画一条分隔线更明确。
  */
  background: var(--af-canvas);
}

.assist-timeline-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  margin: auto;
  padding: 16px 12px;
  text-align: center;
}

.assist-empty-mark {
  font-size: 18px;
  color: var(--af-accent);
  opacity: 0.7;
}

.assist-empty-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-secondary);
}

.assist-empty-text {
  font-size: 11px;
  line-height: 1.6;
  color: var(--af-text-muted);
}

.assist-turn {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 88%;
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
  padding: 8px 11px;
  font-size: 12px;
  line-height: 1.7;
  /*
    中文长句在窄栏里必须能断行：`break-word` 不够，英文标识符
    （`priority_queue`）不会在无空格处断开，会撑破栏宽。
  */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border-radius: 12px;
}

.assist-turn.is-student .assist-bubble {
  /* 学生的消息 = 强调色底上的文字，所以用反色 token，不是写死白色。
   * 见 tokens.css 里主题重绘时的说明。 */
  color: var(--af-text-inverse);
  background: var(--af-accent);
  border-bottom-right-radius: 4px;
}

.assist-turn.is-assistant .assist-bubble {
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-bottom-left-radius: 4px;
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

/*
  回复里带代码时的填入入口。做成文字链而不是实心按钮：它是这条回复的
  一个后续动作，不该和"发送"抢视觉重量。
*/
.assist-turn-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  font: inherit;
  font-size: 10.5px;
  color: var(--af-accent);
  background: transparent;
  border: 1px solid var(--af-border);
  border-radius: 999px;
  cursor: pointer;
}

.assist-turn-action:hover {
  border-color: var(--af-accent);
  background: var(--af-accent-subtle);
}

.assist-turn-action-icon {
  font-size: 11px;
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

/* --- 一次性产出 ------------------------------------------------------- */

.assist-reply {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
  max-height: 240px;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  background: var(--af-elevated);
}

.assist-reply-reason {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-secondary);
}

.assist-code {
  margin: 0;
  padding: 8px;
  max-height: 180px;
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
  padding: 4px 7px;
  font-family: monospace;
  font-size: 11px;
  text-align: left;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.assist-candidate:hover {
  border-color: var(--af-accent);
}

/* --- 底部工具条 ------------------------------------------------------- */

/*
  输入区。
  注意它**不**画分隔线：分隔线只有 `.assist-bar` 那一条 —— 两者会同时
  出现（三档以上），各画一条会变成两条平行线夹住中间的空当。
  输入区有没有上边框由 `.assist-bar` 上的选择器统一决定。
*/
.assist-composer {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}

/*
  工具条下段：动作按钮 + 两个弹层按钮。
  它**不**跟着输入区一起消失 —— 一档的本机补全就是这次作业允许的全部
  帮助，把工具条整块藏掉等于对学生说"这道题没有 AI"。

  它也是弹层的定位参照（`.assist-pop` 相对它向上弹），所以自己
  `position: relative`，弹层因此占满整条工具条的宽度。
  分隔线画在这里：它是"对话区"与"操作区"之间唯一那条界线。
*/
.assist-bar {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
  padding-top: 8px;
  border-top: 1px solid var(--af-border);
}

/*
  输入行：输入框 + 发送按钮并排。
  上一版是"输入框一行、发送按钮另起一行"，在窄面板里一个孤零零的左对齐
  按钮看起来像第三块功能区。合成一行后它们读作一个整体 —— 右边那个按钮
  就是"把这句话发出去"。
*/
.assist-compose {
  display: flex;
  align-items: flex-end;
  gap: 6px;
}

.assist-input {
  flex: 1;
  min-width: 0;
  /* 单行起步、最高约四行：再多就把对话区挤没了。 */
  min-height: 34px;
  max-height: 96px;
  padding: 8px 10px;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  resize: none;
  transition: border-color 0.15s;
}

.assist-input::placeholder {
  color: var(--af-text-muted);
}

.assist-input:focus {
  outline: none;
  border-color: var(--af-accent);
}

.assist-input:disabled {
  opacity: 0.6;
}

.assist-submit {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  height: 34px;
  padding: 0 12px;
  font: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--af-text-inverse);
  background: var(--af-accent);
  border: 1px solid transparent;
  border-radius: var(--af-radius-md);
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}

.assist-submit:hover:not(:disabled) {
  background: var(--af-accent-hover);
}

.assist-submit:disabled {
  color: var(--af-text-muted);
  background: var(--af-elevated);
  border-color: var(--af-border);
  cursor: not-allowed;
}

.assist-submit-icon {
  font-size: 10px;
  opacity: 0.85;
}

/* 标签行：这一句会走哪条路 + 快捷键提示，都压到 10.5px 的辅助文字。 */
.assist-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: -2px 0 0;
  font-size: 10.5px;
  color: var(--af-text-muted);
}

.assist-label {
  font-weight: 600;
  color: var(--af-text-secondary);
}

.assist-tag {
  padding: 0 5px;
  font-size: 9.5px;
  color: var(--af-warning);
  border: 1px solid var(--af-warning);
  border-radius: 999px;
}

.assist-keyhint {
  margin-left: auto;
  white-space: nowrap;
}

/*
  两个弹层按钮并排。它们都是"当前设定"——收在按钮上、点开才展开可选值，
  这样工具条的高度恒定，不会因为模型多了一个而变高。
*/
.assist-switches {
  display: flex;
  gap: 6px;
}

.assist-switch {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  min-width: 0;
  padding: 5px 9px;
  font: inherit;
  font-size: 11px;
  color: var(--af-text-muted);
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
  cursor: pointer;
  transition:
    border-color 0.15s,
    color 0.15s;
}

.assist-switch:hover {
  color: var(--af-text-primary);
  border-color: var(--af-border-strong);
}

.assist-switch[aria-expanded='true'] {
  color: var(--af-text-primary);
  border-color: var(--af-accent);
}

.assist-switch-icon {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--af-accent);
}

.assist-switch-label {
  flex-shrink: 0;
}

.assist-switch-value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--af-text-primary);
}

.assist-switch-caret {
  flex-shrink: 0;
  font-size: 9px;
  opacity: 0.7;
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

/* --- 一次性动作 ------------------------------------------------------- */

.assist-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* --- 弹层 ------------------------------------------------------------- */

/*
  弹层从工具条**向上**弹。
  工具条贴在面板底边，向下弹会被面板的 `overflow: hidden` 直接切掉。
  用 absolute 而不是 fixed/NModal：它是这个面板内部的一块扩展，不该
  盖住整个应用，也不该抢焦点。
*/
.assist-pop {
  position: absolute;
  z-index: 30;
  right: 0;
  left: 0;
  bottom: calc(100% + 6px);
  padding: 8px;
  background: var(--af-glass-elevated, var(--af-elevated));
  /* Both prefixes, unlike the rest of this file: the token is a raw CSS
   * value rather than a Tailwind utility, and Tailwind is what normally
   * supplies the `-webkit-` form. */
  -webkit-backdrop-filter: var(--af-glass-blur, none);
  backdrop-filter: var(--af-glass-blur, none);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-md);
  box-shadow: var(--af-shadow-md, 0 8px 24px rgb(0 0 0 / 0.35));
}

.assist-pop-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 8px;
}

.assist-pop-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.assist-pop-hint {
  font-size: 10px;
  color: var(--af-text-muted);
}

.assist-pop-close {
  margin-left: auto;
  padding: 0 4px;
  font: inherit;
  font-size: 11px;
  color: var(--af-text-muted);
  background: none;
  border: none;
  cursor: pointer;
}

.assist-pop-close:hover {
  color: var(--af-text-primary);
}

/* 模型弹层：现在是项目列表而不是下拉框，所以去掉 ul 的默认外观。 */
.assist-pop-model {
  margin: 0;
  padding: 4px;
  list-style: none;
}

.assist-model-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 100%;
  padding: 7px 9px;
  font: inherit;
  font-size: 11.5px;
  color: var(--af-text-secondary);
  background: none;
  border: none;
  border-radius: var(--af-radius-sm);
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

/* 模型列表与「配置自定义 API」之间的分隔线。 */
.assist-model-divider {
  height: 1px;
  margin: 3px 2px;
  background: var(--af-border);
}

/* --- 配置 API 窗口 ------------------------------------------------------ */
/*
  窗口内容。NModal 会 teleport 到 body，但这些元素的 `data-v-*` 作用域
  属性跟着元素走，scoped 样式照样命中。
*/

.assist-config {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.assist-config-hint {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--af-text-muted);
}

.assist-config-hint code {
  padding: 1px 5px;
  font-size: 10.5px;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border-radius: 4px;
}

.assist-config-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.assist-config-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--af-text-secondary);
}

.assist-config-input {
  width: 100%;
  padding: 7px 9px;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  transition: border-color 0.15s;
  box-sizing: border-box;
}

.assist-config-input::placeholder {
  color: var(--af-text-muted);
}

.assist-config-input:focus {
  outline: none;
  border-color: var(--af-accent);
}

.assist-config-input:disabled {
  opacity: 0.6;
}

/* 状态行：成功用主题色；失败用主文字色 + 虚线框，与面板"被拒不是错误"
   的口径一致，不引入这套主题没有定义的红色。 */
.assist-config-status {
  margin: 0;
  padding: 6px 8px;
  font-size: 11.5px;
  line-height: 1.5;
  border-radius: var(--af-radius-sm);
}

.assist-config-status.is-ok {
  color: var(--af-accent);
  padding: 0 8px;
  background: none;
  border: none;
}

.assist-config-status.is-err {
  color: var(--af-text-primary);
  background: var(--af-canvas);
  border: 1px dashed var(--af-border-strong);
}

.assist-config-note {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-warning);
}

.assist-config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

/* --- 提示文字 --------------------------------------------------------- */

.assist-note {
  margin: 0;
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.assist-note-error {
  color: var(--af-text-primary);
}

/*
  成功提示刻意**不用**绿色：这套主题的强调色（`--af-accent`）才是"这是
  你的选择已生效"的语言，而绿/红在这套暗色主题里没有定义，随手写死一个
  `#4ade80` 会在 `terminal` / `paper` 两套皮肤下变成外来色。
*/
.assist-note-ok {
  color: var(--af-accent);
}

/* --- 小按钮 ----------------------------------------------------------- */

/*
  面板里的按钮比页面工具栏的小一号：窄栏里，13px/16px 的 toolbar 尺寸
  会把两个按钮挤成两行。
*/
.btn {
  padding: 6px 16px;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.btn-sm {
  padding: 5px 10px;
  font-size: 11.5px;
  font-weight: 500;
}

.btn:disabled {
  color: var(--af-text-muted);
  background: var(--af-elevated);
  border-color: var(--af-border);
  cursor: not-allowed;
}

.btn-secondary {
  color: var(--af-text-secondary);
  background: var(--af-canvas);
  border-color: var(--af-border);
}

.btn-secondary:hover:not(:disabled) {
  color: var(--af-text-primary);
  border-color: var(--af-border-strong);
}

.btn-primary {
  color: var(--af-text-inverse);
  background: var(--af-accent);
}

.btn-primary:hover:not(:disabled) {
  background: var(--af-accent-hover);
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
