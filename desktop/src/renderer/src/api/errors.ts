/**
 * 把一次失败的请求翻译成学生能读懂、能据此行动的一句话。
 *
 * 为什么需要这一层
 * ----------------
 * 直接把 axios 的 `err.message` 渲染出来，学生看到的是
 * `Request failed with status code 404` —— 这句话信息量是零：它说了
 * 没有，但没说是**什么**没有、为什么没有、以及他该怎么办。而这三件事
 * 对不同的失败原因是完全不同的答案：
 *
 * - **404** —— 资源不在。发生在题目上时，几乎只有一个成因：题库被重新
 *   导入过（`tools/load_problems.py --replace` 会删旧行、插新行），
 *   数据库自增 id 于是往前跳；而作业、错题本、浏览历史里存的还是旧 id。
 *   学生从作业里点进来就会撞上这个。界面要说清"这题已经不在题库里了"，
 *   并给一条出路，而不是让他对着一个 HTTP 状态码发呆。
 * - **401/403** —— 身份问题。学生要能区分"我没登录"和"我不被允许"。
 * - **超时 / 无响应** —— 后端没答。按本机的经验，这通常是数据库假活
 *   （端口在监听但没人 accept），重试是没用的，要去看服务。
 * - **其余** —— 退回服务端自己写的 `detail`，再退回 axios 的 message。
 *
 * 与 `Practice.vue` 里 `describeDebugError` 的关系
 * ------------------------------------------------
 * 那个函数只服务调试面板，且不认识 404。这里做成共用工具，是因为"把
 * 状态码翻译成人话"这件事在题目、作业、错题本上都是同一个需求，各写
 * 一份必然会各自漂移。
 */

import axios from 'axios'

/** 一句话的错误描述，外加一个可选的行动建议。 */
export interface FriendlyError {
  /** 主句：发生了什么。直接展示给学生。 */
  message: string
  /**
   * 可选：一句话说明他能做什么。
   *
   * 与 {@link message} 分开，是因为不是每种失败都有出路 —— 硬凑一句
   * "请稍后重试"只会稀释真正有建议的那几条。
   */
  hint?: string
}

/** 从 axios 错误体里取服务端写的 `detail`（它通常比状态码有用）。 */
function serverDetail(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail.trim() : ''
}

/** 从 axios 错误里取 HTTP 状态码，取不到返回 `null`。 */
export function statusOf(err: unknown): number | null {
  if (!axios.isAxiosError(err)) return null
  return err.response?.status ?? null
}

/** 网络层失败（压根没收到响应）与超时的判别。 */
function isTimeout(err: unknown): boolean {
  return axios.isAxiosError(err) && err.code === 'ECONNABORTED'
}

function isNoResponse(err: unknown): boolean {
  return axios.isAxiosError(err) && !err.response && !isTimeout(err)
}

/**
 * 生成一句人话。
 *
 * `subject` 是"什么东西没加载出来"，用于把同一套状态码翻译成贴合场景的
 * 说法 —— "题目不存在"和"作业不存在"student 能做的下一步不同。
 */
export function describeRequestError(err: unknown, subject = '内容'): FriendlyError {
  const status = statusOf(err)

  if (status === 404) {
    return {
      message: `${subject}不存在。`,
      hint: `它可能已经被删除，或者题库重新导入过导致编号变了。返回列表重新选一次即可。`
    }
  }

  if (status === 401) {
    return {
      message: '登录状态已失效。',
      hint: '重新登录后再试。'
    }
  }

  if (status === 403) {
    return {
      message: `没有查看这个${subject}的权限。`,
      hint: '如果你认为这是错的，请联系老师。'
    }
  }

  if (isTimeout(err)) {
    return {
      message: '服务响应太慢，已经超时。',
      hint: '分析引擎可能正在忙或数据库无响应，稍等片刻再试。'
    }
  }

  if (isNoResponse(err)) {
    return {
      message: '连不上分析引擎。',
      hint: '后台服务可能没有运行，或者刚刚崩溃了。'
    }
  }

  const detail = serverDetail(err)
  if (detail) return { message: detail }

  if (err instanceof Error && err.message) return { message: err.message }

  return { message: `${subject}加载失败。` }
}

/** 只要主句时的简写。 */
export function describeRequestErrorText(err: unknown, subject = '内容'): string {
  const { message, hint } = describeRequestError(err, subject)
  return hint ? `${message}${hint}` : message
}
