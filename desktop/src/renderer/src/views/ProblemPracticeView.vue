<script setup lang="ts">
/**
 * Route adapter for practising a catalogue problem (PU-7).
 *
 * `/problems/:id` renders this, not `Practice.vue` directly, for one
 * reason: `Practice` should not know the router exists. It takes a
 * `problemId` prop and emits `exit`, which keeps it usable as a plain
 * component — and testable without mounting a router — while the router
 * stays a shell concern.
 *
 * That means "exit to free run" needs translating somewhere, and this is
 * the somewhere: leaving a problem is a navigation to the free-run route,
 * not a state change in the page. Free-run mode is simply the absence of
 * an id, which is what `/practice` means.
 *
 * The prop arrives already coerced to a number by the route record's
 * `props` function; the `(\\d+)` constraint on the path is what makes
 * that safe (see `router/index.ts`).
 */

import { useRouter } from 'vue-router'
import Practice from './Practice.vue'

const props = defineProps<{
  problemId: number
  /**
   * The homework this attempt belongs to, when opened from one (阶段 5).
   *
   * It arrives as `null` for a problem opened from the catalogue or lesson,
   * which is the same state as free practice: no assignment means the
   * assistance level falls back to the server default. Passing it through
   * is what lets the AI panel show the level the teacher actually set,
   * instead of the default it used to be stuck on.
   */
  assignmentId?: number | null
}>()

const router = useRouter()

/**
 * `replace`, not `push`: exiting a problem is a correction of where you
 * are, not a place to come back to. Pushing would put `/problems/12` in
 * history behind `/practice`, so the back button would re-enter the
 * problem the user just left.
 */
function exitToFreeRun(): void {
  void router.replace({ name: 'practice' })
}

/**
 * 「下一题」：跳到下一道题，保持同一份作业的上下文。
 *
 * 用 `replace` 而不是 `push` 有同样的理由：逐题推进是一条链，不是一条
 * 会往回走的历史 —— 点"下一题"五次，再按返回键不该一步一步退回前四题。
 * `assignment` query 原样带下去，AI 辅助强度才不会在换题时丢成默认档。
 */
function goNext(problemId: number): void {
  void router.replace({
    name: 'problem',
    params: { id: problemId },
    query: props.assignmentId === null ? {} : { assignment: props.assignmentId }
  })
}
</script>

<template>
  <Practice
    :problem-id="props.problemId"
    :assignment-id="props.assignmentId ?? null"
    @exit="exitToFreeRun"
    @go-next="goNext"
  />
</template>
