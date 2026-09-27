<script setup lang="ts">
/**
 * TeacherGate — 教师端每个页面的身份闸门（2026-09-26）。
 *
 * 三个状态，且前两个都不是错误状态：
 *
 *   1. 会话恢复中   —— 启动时那次往返还没回来。
 *   2. 未登录       —— 一张路牌，指向共享的 `/login`。
 *   3. 已登录但不是教师 —— 一句陈述，外加一条出路。
 *
 * 为什么抽成组件
 * --------------
 * 教师端拆成五个面板之后，这四行判断要出现在五个文件里。它是产品边界
 * （谁能看见学情），不是某个页面的排版细节；五份副本意味着有一天会有一
 * 份忘了改，而那种错误的表现是"某个页面让学生看到了数据"——服务端的
 * 403 会挡住，但页面上什么都不说的话，学生看到的就是一片空白。
 *
 * **这不是安全边界。** 这里的判断是便利，不是闸门：每一条教师接口都在
 * 服务端拒绝非教师（见 `app/api/teacher.py`）。这个组件能少一份的原因，
 * 恰恰是它拦不住任何一个真想进来的人。
 *
 * 关于 `signOut` 事件
 * -------------------
 * "已登录但不是教师"那一路的出路是**登出**，而登出要清空调用方的数据。
 * 所以这个组件不自己调 `logout()`，它 emit 一个事件，由调用方处理——
 * 只有调用方知道该清哪些 ref。
 */

import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { isLoggedIn, sessionUser } from '../api/session'

/** 登录页的地址。共享的那一个，不分师生。 */
const router = useRouter()
const loginHref = computed(() => router.resolve({ name: 'login' }).href)

defineProps<{
  /** 会话是否已经问过服务端了。未确定时不渲染任何结论。 */
  ready: boolean
  /** 是否教师。只有它为真时调用方才渲染真正的内容。 */
  isTeacher: boolean
  /** 未登录那张卡片的小标题。各面板按语境自己写。 */
  loginTitle: string
  /** 未登录那张卡片的一句话解释。 */
  loginBody: string
}>()

// 声明在 `<script setup>` 里，所以下面那个 `$emit` 在模板里是有类型的。
// 早先版本用第二个 `<script>` 块声明 emits，eslint 的
// `vue/require-explicit-emits` 看不见它——两个 script 块的合并顺序不是
// 它检查时能依赖的东西。
defineEmits<{ (e: 'signOut'): void }>()
</script>

<template>
  <div v-if="!ready" class="af-t-placeholder">正在恢复登录状态…</div>

  <div v-else-if="!isLoggedIn" class="gate">
    <h2 class="gate-title">{{ loginTitle }}</h2>
    <p class="gate-body">{{ loginBody }}</p>
    <!-- 这里唯一的控件是导航而不是提交，所以用 NButton 的 tag 换成 <a>
         语义：屏幕阅读器和右键"在新标签打开"都还认它是链接。 -->
    <NButton type="primary" tag="a" :href="loginHref" class="gate-link">去登录</NButton>
  </div>

  <div v-else-if="!isTeacher" class="gate">
    <h2 class="gate-title">此账号不是教师账号</h2>
    <p class="gate-body">
      当前登录的是「{{ sessionUser?.display_name || sessionUser?.username }}」（学生）。
      学情看板只有教师账号可以打开——这不是前端的限制，服务端每一条教师接口都会拒绝学生账号。
    </p>
    <NButton type="primary" class="gate-link" @click="$emit('signOut')">退出登录</NButton>
  </div>

  <slot v-else />
</template>

<style scoped>
/* 三个非控制台状态共用的卡片。放这里而不是共享样式表：只有这个组件
 * 会画它。 */
.gate {
  max-width: 420px;
  padding: 24px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-md);
}

.gate-title {
  margin: 0 0 8px 0;
  font-size: 16px;
  font-weight: 600;
}

.gate-body {
  margin: 0 0 16px 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--af-text-secondary);
}

/* 卡片里唯一的控件靠左，和正文对齐。 */
.gate-link {
  margin-top: 4px;
  text-decoration: none;
}
</style>
