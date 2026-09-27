/**
 * 教师端面板共用的取值与格式化（2026-09-26）。
 *
 * 为什么单独一个模块
 * ------------------
 * 拆分前这些函数长在 `TeacherView.vue` 里，每个面板各拿一份。它们看着像
 * 一行代码的琐事，其实每一条都记着一个判断：
 *
 *   - `num` 允许数字缺席。控制台的类型是**手写**对着服务端的 BaseModel 的
 *     （见 `api/teacher.ts`），一旦漂了，诚实的输出是单元格里一个破折号，
 *     不是老师马上要念出口的表格里出现 `undefined`。
 *   - `percent` 同理，且固定一位小数：同一列里 41% 和 41.7% 混排会让
 *     眼睛误判大小。
 *   - `causeLabel` 把 `null` 显示成「未分析」而不是补一个空字符串。
 *     `null` 不是缺失的标签，是**分析还没落库的那一组**；它是读数，不是
 *     错误（见 `TeacherAnalysisView` 里的 `analysisBehind`）。
 *   - `progressText` 把"没提交"和"提交了但没通过"分开。破折号会读成
 *     "没有数据"——正好是接口特意保下来的那个区分。
 *
 * 这些判断抄五份就会漂，所以只有一份。
 */

/** 一个允许缺席的数字。缺席渲染成破折号，不是 `undefined`。 */
export function num(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '—'
}

/** 一个允许缺席的比率，固定一位小数。 */
export function percent(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—'
}

/** 把接口错误翻成一句人话。优先用服务端的 `detail`。 */
export function messageOf(err: unknown, fallback: string): string {
  const anyErr = err as { response?: { data?: { detail?: string } }; message?: string }
  return anyErr?.response?.data?.detail ?? anyErr?.message ?? fallback
}

/** HTTP 状态码，取不到时 `undefined`。 */
export function statusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status
}

/**
 * 错因分布里某一行的显示名。
 *
 * `null` 不是要补掉的缺失标签——它是一组分析尚未落库的条目，有它自己的
 * 措辞。
 */
export function causeLabel(row: { label: string | null; description: string }): string {
  return row.description || row.label || '未分析'
}

/** 这一行是不是"还没分析"那一组。 */
export function isUnanalysed(row: { label: string | null }): boolean {
  return row.label === null
}

/** 判题状态的中文。未知状态原样透出，不吞掉。 */
export function statusText(status: string | null): string {
  if (!status) return '—'
  const known: Record<string, string> = {
    AC: '通过',
    WA: '答案错误',
    TLE: '超时',
    MLE: '内存超限',
    CE: '编译错误',
    RE: '运行错误'
  }
  return known[status] ?? status
}

/** 时间只到分钟：扫历史的人要知道**是那一节课的什么时候**，不是毫秒。 */
export function dateText(value: string | null): string {
  if (!value) return '—'
  return value.slice(0, 16).replace('T', ' ')
}

/**
 * 成绩单里一行的进度读法。
 *
 * `null` 得到一个词，不是一个破折号：进度缺失意味着"从没交过"，而破折号
 * 读起来是"没有数据"——正是接口特意保下来的那个区分。
 */
export function progressText(row: { progress: number | null; submissions: number }): string {
  if (row.submissions === 0) return '未提交'
  return percent(row.progress ?? 0)
}

/** 复核徽标的文字。`null` / `undefined` 是没有判定过，不是判定为空串。 */
export function reviewText(verdict: string | null | undefined): string {
  if (verdict === 'confirmed') return '已确认'
  if (verdict === 'dismissed') return '已驳回'
  return ''
}
