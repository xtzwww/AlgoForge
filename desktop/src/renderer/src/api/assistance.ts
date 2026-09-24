/**
 * AI 辅助强度（阶段 5 · 因材施教）。
 *
 * 五档的定义在服务端（`app/assistance.py`），这里只是它的手写镜像 ——
 * 与 `teacher.ts` 同样的理由：两侧不同语言、构建里没有 codegen，字段名
 * 对不上时表现为界面上少一个数字，所以每个字段都用类型钉住。
 *
 * 两件事前端**故意不做**
 * ----------------------
 * 1. **不传档位数字**。请求里只有 `assignment_id`，档位由服务端按作业查。
 *    档位若由客户端自称，改一个数字就能拿到五档，老师压的强度等于没有。
 *    这里的 `listLevels()` 是为了**渲染说明**，不是为了判断权限。
 * 2. **不把 `allowed` 当成结果来看**。被拒不是错误：返回是 200 +
 *    `allowed=false` + `reason`，界面该渲染那句解释，而不是一个红色报错。
 *    档位是教学设置，不是故障。
 *
 * 这也是为什么每个入口都返回 `AssistResponse` 而不是抛异常：四种动作在
 * 五档下的可用性是一张表，客户端只该照着 `allowed` 渲染。
 */

import { http } from './http'

/** 一档允许什么。服务端 `policy_to_dict` 的镜像。 */
export interface AssistancePolicy {
  level: number
  /** 给老师看的档位名。 */
  label: string
  /** 给学生看的说法，比 {@link label} 口语。 */
  student_label: string
  /** 一段话说明这一档是什么。 */
  description: string
  /** 补全是否要联网调模型。一档为 false —— 一档的定义就是不联网。 */
  completion_uses_model: boolean
  allows_chat: boolean
  /** 对话只能给提示（回复像代码会被服务端降级）。三档为 true。 */
  chat_is_hint_only: boolean
  allows_code_insertion: boolean
  allows_full_solution: boolean
}

/** 四种动作的名字，与 `models/assistance_usage.py` 的常量一致。 */
export type AssistanceAction = 'complete' | 'chat' | 'insert' | 'full_solution'

export interface AssistRequest {
  /**
   * 作业 id。省略即默认档 —— 自由练习时没有作业，仍应能用 AI。
   * **这是唯一的档位来源**，见模块 docstring。
   */
  assignment_id?: number
  problem_id?: number
  language?: string
}

export interface CompleteRequest extends AssistRequest {
  /** 光标之前的文本。一档要从里面摘标识符。 */
  prefix: string
}

export interface ChatRequest extends AssistRequest {
  question: string
  /** 题干，可空（学生可能只是想问一个语言用法）。 */
  statement?: string
}

export interface InsertRequest extends AssistRequest {
  instruction: string
  current_code?: string
}

export interface SolutionRequest extends AssistRequest {
  statement: string
}

/**
 * 一次辅助请求的结果。
 *
 * `allowed=false` 时 `content` 必为空、`reason` 必非空 —— 客户端只要看这
 * 两个字段就能决定渲染什么，不必理解档位规则。
 */
export interface AssistResponse {
  allowed: boolean
  action: AssistanceAction
  /** 服务端裁决时用的档位，可能与 `listLevels()` 的默认档不同。 */
  level: number
  /** 一句话解释允许或被拒的理由。 */
  reason: string
  content: string
  /** 一档的补全候选词。`content` 为空时界面退到它们。 */
  candidates: string[]
  policy: AssistancePolicy
  /** 三档的对话被判定为代码、已被服务端降级成提示。 */
  downgraded: boolean
}

/** 五档规则，公开。用于渲染说明与老师端的选择器。 */
export async function listLevels(): Promise<AssistancePolicy[]> {
  const res = await http.get<{ levels: AssistancePolicy[] }>('/api/v1/assistance/levels')
  return res.data.levels
}

// --- 模型目录与切换（PU-13）------------------------------------------

/** 一个可选的模型。服务端 `model_catalog.ModelOption` 的镜像。 */
export interface ModelOption {
  /** 请求里用的标识，与 provider 名同值。 */
  id: string
  /** 选单上显示的名字。 */
  label: string
  /** 实际发出去的模型名，作为副标题显示。 */
  model: string
  /** 端点，作 tooltip —— 学生能分辨"本机"还是"要联网"。 */
  base_url: string
  /** true = 不需要密钥（Ollama）。 */
  local: boolean
}

export interface ModelSnapshot {
  /** 当前生效的模型。 */
  active: string
  /** `.env` 里配的默认值，界面用它标注"默认"。 */
  default: string
  models: ModelOption[]
}

/**
 * 列出这台机器上**真的能回答请求**的模型。
 *
 * 配置不全的 provider（例如没填 key 的 deepseek）不会出现在列表里 ——
 * 列出它等于让学生点一下、提问、才收到 401。见 `app/model_catalog`。
 */
export async function listModels(): Promise<ModelSnapshot> {
  const res = await http.get<ModelSnapshot>('/api/v1/assistance/models')
  return res.data
}

/**
 * 切换当前模型。传 `null` 恢复 `.env` 的默认值。
 *
 * 切换在服务端进程内生效，重启回到默认 —— 这是有意的：想固定就用 .env。
 */
export async function setModel(modelId: string | null): Promise<ModelSnapshot> {
  const res = await http.put<ModelSnapshot>('/api/v1/assistance/models', {
    model_id: modelId
  })
  return res.data
}

/** 需要一点代码。一档走本机词表，二档起走模型。 */
export async function complete(req: CompleteRequest): Promise<AssistResponse> {
  const res = await http.post<AssistResponse>('/api/v1/assistance/complete', req)
  return res.data
}

/** 问 AI。三档起；三档只给提示，回复像代码会被服务端降级。 */
export async function chat(req: ChatRequest): Promise<AssistResponse> {
  const res = await http.post<AssistResponse>('/api/v1/assistance/chat', req)
  return res.data
}

/** 让 AI 写一段代码插进编辑器。四档起。 */
export async function insert(req: InsertRequest): Promise<AssistResponse> {
  const res = await http.post<AssistResponse>('/api/v1/assistance/insert', req)
  return res.data
}

/** 完整解法。只有五档放行。 */
export async function solution(req: SolutionRequest): Promise<AssistResponse> {
  const res = await http.post<AssistResponse>('/api/v1/assistance/solution', req)
  return res.data
}

// --- 老师端：使用留痕 -------------------------------------------------

/** 一个学生用了几次。 */
export interface UsageByStudent {
  student_id: number | null
  display_name: string
  count: number
}

/** AI 使用留痕的汇总。 */
export interface AssistanceUsage {
  total: number
  /** 动作 → 次数，键是英文常量。 */
  by_action: Record<string, number>
  /** 动作 → 次数，键是中文名，供直接渲染。 */
  by_action_labeled: Record<string, number>
  by_student: UsageByStudent[]
}

/**
 * 老师端读留痕。
 *
 * `assignmentId` 省略则是全量（含自由练习），用于"这个班整体用了多少"。
 */
export async function getAssistanceUsage(assignmentId?: number): Promise<AssistanceUsage> {
  const res = await http.get<AssistanceUsage>('/api/v1/teacher/assistance/usage', {
    params: assignmentId === undefined ? {} : { assignment_id: assignmentId }
  })
  return res.data
}
