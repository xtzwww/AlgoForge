<script setup lang="ts">
/**
 * S7's hint ladder: three attempts at the same hint, each more concrete.
 *
 * This is deliberately **not** {@link LayeredFeedback}, even though both
 * hand things out one at a time and both are about hints. The difference
 * is what the last step means:
 *
 * - `LayeredFeedback` is one diagnosis cut into 错在哪 → 提示 → 怎么改.
 *   Releasing the last step ends the exercise, because 怎么改 *is* the
 *   answer and there is nothing beyond it.
 * - This ladder is three tries at the same nudge. Rung 1 points at the
 *   area ("看看循环边界"), rung 3 is nearly the answer, and the student
 *   stops at whichever rung unsticks them. Going further is allowed, not
 *   required — there is no step that gives the exercise up.
 *
 * Merging them would break one or the other: the ladder's rungs would
 * become sections that terminate in the fix (so the ladder stops being a
 * ladder), or the diagnosis's fix would become just another rung (so
 * there is no final answer anywhere).
 *
 * Two decisions inherited from `LayeredFeedback`, for the same reasons:
 *
 * 1. *Progress is not persisted.* Leaving and returning resets to no
 *    rungs. The gate is a self-assessment, and a student revisiting
 *    tomorrow is a different learner from today's.
 *
 * 2. *A new ladder starts closed.* Without this, submitting again would
 *    leave the previous problem's rung 3 on screen — the student would be
 *    handed a near-answer to a bug they have not looked at yet, which is
 *    what the gate exists to prevent.
 */
import { computed, ref, watch } from 'vue'
import type { HintLadder } from '../api/mistake_book'

const props = defineProps<{
  /** The ladder to show. Empty levels render nothing, not an empty card. */
  ladder: HintLadder
}>()

/** How many rungs have been released. 0 = nothing shown yet. */
const revealed = ref(0)

/**
 * Rungs in ladder order, by the model's own `level`.
 *
 * Sorted on `level` rather than trusted to arrive sorted, and the label
 * shows `level` rather than the array position: the model declares how
 * deep each rung is, and while the sidecar guarantees the rungs are
 * distinct it does not guarantee they are consecutive. A UI that numbered
 * them 1/2/3 by position would silently present a skipped rung as if
 * nothing were missing.
 */
const rungs = computed(() => [...props.ladder.levels].sort((a, b) => a.level - b.level))

/** Self-check questions, with blanks dropped. */
const selfCheck = computed(() => props.ladder.self_check.filter((q) => q !== ''))

/**
 * A different ladder closes again.
 *
 * Keyed on the ladder object rather than on its text, because the sidecar
 * sends a fresh object per analysis — comparing strings would miss a
 * ladder whose wording happened to be identical to the last one's.
 */
watch(
  () => props.ladder,
  () => {
    revealed.value = 0
  }
)

/** Nothing to show at all — no rungs and no questions. */
const isEmpty = computed<boolean>(() => rungs.value.length === 0 && selfCheck.value.length === 0)

const fullyRevealed = computed<boolean>(() => revealed.value >= rungs.value.length)

/** Label for the button that releases the next rung. */
const revealLabel = computed<string>(() => {
  const labels = ['需要提示', '还是不会，再明确一点', '还是不会，再具体一点']
  const next = revealed.value
  return labels[Math.min(next, labels.length - 1)]
})

function revealNext(): void {
  if (revealed.value >= rungs.value.length) return
  revealed.value += 1
}

/** Collapse back to no rungs at all — for retrying without help. */
function restart(): void {
  revealed.value = 0
}
</script>

<template>
  <div v-if="!isEmpty" class="ladder">
    <div class="ladder-head">
      <span class="ladder-badge">提示阶梯</span>
      <span class="ladder-progress">
        {{ revealed }} / {{ rungs.length }}
      </span>
    </div>

    <div
      v-for="(rung, i) in rungs"
      v-show="revealed > i"
      :key="rung.level"
      class="ladder-rung"
    >
      <span class="ladder-level">第 {{ rung.level }} 级</span>
      <p class="ladder-text">{{ rung.text }}</p>
    </div>

    <!--
      The self-check questions appear once any rung is out, not at the
      end. They are a check on understanding, so the moment to offer them
      is the moment the student thinks they have got it — which may be
      after rung 1. Holding them until rung 3 would ask the student to
      read three hints before being allowed to test themselves.
    -->
    <div v-if="selfCheck.length && revealed > 0" class="ladder-check">
      <span class="ladder-check-title">自己检查一下</span>
      <ul class="ladder-check-list">
        <li v-for="(q, i) in selfCheck" :key="i">{{ q }}</li>
      </ul>
    </div>

    <div class="ladder-actions">
      <button
        v-if="!fullyRevealed"
        class="btn btn-ladder"
        type="button"
        @click="revealNext"
      >
        {{ revealLabel }}
      </button>
      <template v-else>
        <span class="ladder-done-note">提示已经给到这里了，先照着改一遍再提交。</span>
        <button
          class="btn btn-again"
          type="button"
          title="收起所有提示，回到一点都不看"
          @click="restart"
        >
          重新自己想
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.ladder {
  padding: 10px 12px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.ladder-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ladder-badge {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-accent);
  border: 1px solid var(--af-accent);
  border-radius: var(--af-radius-sm);
}

/*
 * How far down the ladder this student got. Shown rather than hidden: it
 * is the one number here that is about the student rather than about the
 * problem, and it makes "you needed rung 3" legible as a fact instead of
 * as a feeling.
 */
.ladder-progress {
  font-size: 12px;
  color: var(--af-text-muted);
}

.ladder-rung {
  margin-top: 10px;
  padding-left: 10px;
  border-left: 2px solid var(--af-border);
}

.ladder-level {
  display: block;
  margin-bottom: 2px;
  font-size: 11px;
  color: var(--af-text-muted);
}

.ladder-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
  /* Model prose contains its own newlines; keep them. */
  white-space: pre-wrap;
  word-break: break-word;
}

.ladder-check {
  margin-top: 10px;
  padding-left: 10px;
  border-left: 2px solid var(--af-accent);
}

.ladder-check-title {
  display: block;
  margin-bottom: 2px;
  font-size: 11px;
  color: var(--af-text-muted);
}

.ladder-check-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

.ladder-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}

.btn {
  padding: 4px 12px;
  font-family: inherit;
  font-size: 12px;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    opacity var(--af-duration-fast) var(--af-ease);
}

.btn-ladder {
  color: var(--af-accent);
  background: transparent;
  border-color: var(--af-accent);
}

.btn-ladder:hover:not(:disabled) {
  background: var(--af-accent-subtle);
}

.ladder-done-note {
  font-size: 12px;
  color: var(--af-text-muted);
}

.btn-again {
  color: var(--af-text-secondary);
  background: transparent;
  border-color: var(--af-border-strong);
}

.btn-again:hover:not(:disabled) {
  color: var(--af-text-primary);
  background: var(--af-surface);
}
</style>
