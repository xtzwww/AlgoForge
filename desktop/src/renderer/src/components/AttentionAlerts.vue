<script setup lang="ts">
/**
 * 「需要关注」——教师端的学情预警列表（阶段 6）。
 *
 * 从 `TeacherView.vue`（1605 行）里抽出来，只为一个理由：它**不拥有状态、
 * 不发请求**。父组件把 `alerts` 推进来，它只决定渲染几条、点了 emit 谁。
 * 这是它能脱离那份视图单独被测的前提——照 `OutputPanel.vue` 的形状做的。
 *
 * 值得钉住的正是「坏了只是该出现的没出现」那类行为：
 *
 * - **一条都没有时整段不渲染。** 渲染出来就是「你该关注 0 个人」，那是一条
 *   纯噪音的标题，比没有更糟。
 * - **每条都带依据（reason）。** 设计上明确不给「风险 87%」这种数字，理由
 *   是没法追问的数字教师不会信；所以依据那一行是这个组件存在的意义，
 *   它丢了组件就只剩一个名字列表。
 * - **点名字是 emit，不是自己跳转。** 去哪个学生、要不要带上下钻参数，
 *   由父组件决定——它才持有名册与路由。
 *
 * 样式随组件一起搬过来了（`TeacherView.vue` 原 1472–1511 行）。必须搬：
 * 父组件是 `<style scoped>`，scoped 只作用到子组件**根元素**，穿透不到内部
 * 的 `li` / `button`，留在父组件里就是样式静默失效。
 */
import type { AlertOut } from '@renderer/api/teacher'

defineProps<{
  alerts: AlertOut[]
}>()

const emit = defineEmits<{
  select: [alert: AlertOut]
}>()
</script>

<template>
  <ul v-if="alerts.length > 0" class="alert-list">
    <li v-for="(alert, index) in alerts" :key="`${alert.user_id}-${index}`">
      <button type="button" class="alert-who" @click="emit('select', alert)">
        {{ alert.display_name }}
      </button>
      <span class="alert-reason">{{ alert.reason }}</span>
    </li>
  </ul>
</template>

<style scoped>
.alert-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.alert-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
  padding: 8px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--af-border);
}

.alert-list li:last-child {
  border-bottom: none;
}

.alert-who {
  padding: 0;
  font-size: 13px;
  font-family: inherit;
  font-weight: 600;
  color: var(--af-accent);
  background: none;
  border: none;
  cursor: pointer;
}

.alert-who:hover {
  text-decoration: underline;
}

/* The reason is the point of the row, so it gets the room. */
.alert-reason {
  flex: 1 1 260px;
  color: var(--af-text-secondary);
}
</style>
