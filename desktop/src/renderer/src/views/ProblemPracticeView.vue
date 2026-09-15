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

const props = defineProps<{ problemId: number }>()

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
</script>

<template>
  <Practice :problem-id="props.problemId" @exit="exitToFreeRun" />
</template>
