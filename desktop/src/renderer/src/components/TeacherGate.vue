<script setup lang="ts">
/**
 * TeacherGate — 教师端页面的**展示层**兜底（2026-09-26；2026-10-04 降级）。
 *
 * 它过去是"路由之外的第二个门禁"：自己读 `isLoggedIn` / `isTeacher`，
 * 三个状态各画一个卡片，还把 `signOut` 事件发回给调用方。八个副本里的
 * 一个就是它。
 *
 * **现在它是纯展示组件。** 门已经收在路由表上（`meta.requiresTeacher`
 * + `router/guards/auth.ts`）：能渲染到这里的页面，浏览器里一定是教师
 * 账号，所以"未登录"和"不是教师"这两屏**正常情况下不会再出现**。
 *
 * 那为什么还留着它？
 * ------------------
 * 因为有一个它挡得住、守卫挡不住的情况：**会话在页面已经挂载之后才
 * 失效**。最常见的是一条教师接口返回 401（token 十二小时后过期），
 * `api/session.ts` 的清理由调用方触发，而守卫只在**导航时**跑——不回
 * 首页就不会重新判一次。此时页面还立在那里，而它手里的数据已经全部
 * 过期。这一屏说的是"这个账号现在不是教师了"，给一条出路。
 *
 * 换句话说：**守卫管"进不来"，它管"进来了但身份没了"**。
 *
 * `roleLost` 是 prop 而不是自己从 session 读——保持它无状态、可单独渲染，
 * 也让"页面自己决定要不要用它"这件事保留在页面手里。**默认 `false`**，
 * 因为守卫已经保证"进得来就是教师"；页面只在真的检测到身份失效时传
 * `true`（目前是备用状态：六个面板都还没接这根线，接法见 `roleLost` 的
 * 文档）。
 *
 * `signOut` 事件保留：那一屏的出路是**登出**（换一个教师账号），而登出
 * 要清空调用方持有的数据（各面板的 ref），只有调用方知道该清什么。所以
 * 这里不自己调 `logout()`。
 *
 * 不再需要 `useRouter().resolve()` 算登录页地址：出路是"退出登录"而不是
 * "去登录"——人已经登录着了，只是角色不对，再去一次登录页说不通，与
 * `router/guards/auth.ts` 里"角色不符回首页而不是登录页"是同一条判断。
 */
import { NButton } from 'naive-ui'
import { sessionUser } from '../api/session'

/**
 * `roleLost`：内容本身没问题，但身份已经不是教师了。
 *
 * **默认 `false`** —— 守卫已保证"进得来就是教师"，所以正常情况下所有
 * 页面都不传它，直接铺内容。页面只在**自己发现会话失效**时传 `true`：
 * 判据是它发的教师接口回了 401（`api/session.ts` 那一路会把 user 清掉）。
 * 那种情况下这一屏是最后一道用户可读的提示，而不是一道门。
 */
defineProps<{ roleLost?: boolean }>()

defineEmits<{ (e: 'signOut'): void }>()
</script>

<template>
  <slot v-if="!roleLost" />

  <div v-else class="gate">
    <h2 class="gate-title">此账号不是教师账号</h2>
    <p class="gate-body">
      当前登录的是「{{ sessionUser?.display_name || sessionUser?.username }}」。
      学情看板只有教师账号可以打开——这不是前端的限制，服务端每一条教师接口都会拒绝学生账号。
    </p>
    <NButton type="primary" class="gate-link" @click="$emit('signOut')">退出登录</NButton>
  </div>
</template>

<style scoped>
/* 只有这一个状态会画卡片。放这里而不是共享样式表：只有这个组件用。 */
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
