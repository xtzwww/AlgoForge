/**
 * 教师端面板的加载生命周期（2026-09-26）。
 *
 * 这一段在拆分前是每个面板都要抄的一小段仪式：等会话往返回来、确认是
 * 教师、拉数据、老师重新登录时再拉一次。抄五份的风险不是多写几行，是
 * **有一份会在"还没确定身份时就拉数据"** ——那种错误的表现是把"你不是
 * 教师"闪给一位教师看，而它只在冷启动的那几百毫秒里出现。
 *
 * `sessionReady` 而不是 `isTeacher` 单独判定，就是这个原因：往返没回来
 * 之前，`isTeacher` 对**所有人**都是 false。
 */

import { onMounted, watch, type Ref } from 'vue'
import { isTeacher, sessionReady } from '../../api/session'

export interface TeacherPanel {
  /** 会话是否已经问过服务端。够不够渲染任何结论，看它。 */
  ready: Ref<boolean>
  /** 是否是教师。 */
  teacher: Ref<boolean>
  /** 老师登出时调用：面板自己知道要清哪些 ref。 */
  reset: () => void
}

/**
 * 把"身份就绪后加载"这段样板装好。
 *
 * @param load  真正的取数函数。它自己要负责在非教师时提前返回。
 * @param reset 登出时清空本面板的状态。
 */
export function useTeacherPanel(load: () => void | Promise<void>, reset: () => void): TeacherPanel {
  // `immediate` 是为了已经登录过的热启动：watch 注册时 `isTeacher` 可能
  // 已经是 true，不立即跑一次就要等下一次身份变化——而它不会再来。
  watch(
    () => [sessionReady.value, isTeacher.value] as const,
    ([ready, teacher]) => {
      if (ready && teacher) void load()
    },
    { immediate: true }
  )

  // `watch` 只在**变化**时触发，而冷启动时 `isTeacher` 往往从 false 变
  // true，所以上面那条已经覆盖了。`onMounted` 这条兜住另一种顺序：
  // 会话在组件挂载前就已经解析完，此时 watch 的 immediate 也跑过了；
  // 两条都跑只会重复一次请求，而漏掉一条的表现是空白页。
  onMounted(() => {
    if (sessionReady.value && isTeacher.value) void load()
  })

  return {
    ready: sessionReady,
    teacher: isTeacher,
    reset
  }
}
