<script setup lang="ts">
/**
 * AuthLayout — 登录页与注册页的共用外壳（2026-10-04）。
 *
 * 两个页面共享的东西只有**形状**：一个居中的、玻璃质感的窄卡片，卡片
 * 上方是身份切换器，下方是页脚提示。这些原本在 `LoginView` 和
 * `RegisterView` 里各写了一遍（两份 `flex flex-1 items-center`、
 * 两份 `max-w-sm`、两份 `af-glass rounded-[var(--af-radius-lg)] p-6`、
 * 两份 `mt-4 text-center text-[11px]` 页脚）。改了其中一份，两个页面的
 * 卡片在同一个屏幕上会不一样宽——而使用者是从一个走到另一个的。
 *
 * **只包外壳，不包表单。** 表单留在各自页面里：登录有 2 个字段、注册
 * 有 4 个，且注册多一条"两次密码不一致"的本地校验。把它们塞进同一个
 * 组件、靠 `v-if` 切换，正是 `RegisterView` 文档里反对的那件事——
 * 每个条件分支都是登录路径顺带捡起一条注册规则的机会。
 *
 * **全屏（2026-10-04）。** 这一层现在撑满整个视口：`App.vue` 对
 * `meta.layout === 'auth'` 的路由不渲染侧边栏/顶栏，所以这里是页面上
 * 唯一的结构，`flex-1` 才有可分配的父高度。`overflow-y-auto` 是给矮
 * 窗口留的退路——注册页四个字段在 600px 高的窗口里放不下。
 *
 * 三个插槽对应三个区域：
 *   - `switcher` 学生/老师切换器（两个页面的按钮不同，所以由页面给）
 *   - 默认插槽 表单本体
 *   - `footer`   卡片下方那行小字
 */
</script>

<template>
  <div class="flex flex-1 items-center justify-center overflow-y-auto p-6">
    <div class="w-full max-w-sm">
      <div class="af-glass rounded-[var(--af-radius-lg)] p-6">
        <slot name="switcher" />
        <slot />
      </div>
      <p class="mt-4 text-center text-[11px] leading-relaxed text-af-muted">
        <slot name="footer" />
      </p>
    </div>
  </div>
</template>
