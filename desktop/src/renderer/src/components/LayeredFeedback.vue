<script setup lang="ts">
/**
 * One AI diagnosis, released one section at a time.
 *
 * The analysis produces three sections meant to be read in order:
 * 错在哪 → 提示 → 怎么改. Rendering them together makes the last one the
 * only one that gets read — the student scrolls past the diagnosis
 * straight to the fix, and the exercise of locating their own bug never
 * happens. So the panel hands them out one at a time behind a button the
 * student presses only when they are still stuck. The pedagogic name for
 * this is scaffolding: support is withdrawn gradually as the learner
 * takes over, and it only works if the learner gets to struggle first.
 *
 * Two decisions worth stating, both inherited from the mistake book
 * where this behaviour was first built:
 *
 * 1. *Progress is not persisted.* It lives in this component, so leaving
 *    the page and coming back resets to the first layer. That is
 *    deliberate: the gate is a self-assessment, and a student who
 *    returns to a mistake the next day is a different learner from
 *    yesterday's. Persisting "already seen the answer" would reward the
 *    revisit with nothing.
 *
 * 2. *The reveal is bounded by what exists.* If the model produced no
 *    hint, pressing 还是不会 jumps straight to the fix rather than
 *    showing an empty section — a door that opens onto an empty room
 *    reads as a bug.
 *
 * The component owns its release state, so each instance is independent:
 * the mistake book renders one per entry and the practice page renders
 * one for the submission just judged, with no shared bookkeeping.
 */
import { computed, ref, watch } from 'vue'
import {
  FEEDBACK_REVEAL_LABELS,
  FEEDBACK_SECTION_TITLES,
  type FeedbackSection
} from '../api/mistake_book'

const props = withDefaults(
  defineProps<{
    /** 错在哪 — what is actually wrong. */
    errorDetail: string
    /** 提示 — a nudge that does not give the fix away. */
    hint: string
    /** 怎么改 — the answer. */
    suggestedFix: string
    /** The error label in student-facing Chinese, when known. */
    errorTypeDescription?: string
    /** The raw machine label, shown dimmed as metadata. */
    errorType?: string | null
  }>(),
  { errorTypeDescription: '', errorType: null }
)

/** How many sections have been released. 0 = nothing shown yet. */
const revealed = ref(0)

/**
 * A new diagnosis starts closed again.
 *
 * Without this, submitting a second time would leave the *previous*
 * answer's panels open — the student would be handed the fix for a bug
 * they have not looked at yet, which is the exact failure the gate
 * exists to prevent.
 */
watch(
  () => [props.errorDetail, props.hint, props.suggestedFix],
  () => {
    revealed.value = 0
  }
)

const prose = computed<Record<FeedbackSection, string>>(() => ({
  error_detail: props.errorDetail,
  hint: props.hint,
  suggested_fix: props.suggestedFix
}))

/**
 * The sections this diagnosis actually has, in reading order.
 *
 * Not always all three: the model omits a section when it has nothing to
 * say, and rows written before the split existed carry everything under
 * `error_detail`. Filtering the empty ones is what lets the buttons skip
 * over gaps instead of showing blank panels.
 */
const sections = computed<FeedbackSection[]>(() => {
  const order: FeedbackSection[] = ['error_detail', 'hint', 'suggested_fix']
  return order.filter((s) => prose.value[s] !== '')
})

function isRevealed(section: FeedbackSection): boolean {
  return revealed.value > sections.value.indexOf(section)
}

/**
 * Keyed off the *diagnosis's* section count rather than the fixed three,
 * so one with no hint is fully revealed after two presses.
 */
const fullyRevealed = computed<boolean>(() => revealed.value >= sections.value.length)

/** Label for the button that releases the next section, if any. */
const revealLabel = computed<string>(() => {
  const next = sections.value[revealed.value]
  return next ? FEEDBACK_REVEAL_LABELS[next] : ''
})

/** The first press opens 错在哪; later ones go deeper. */
function revealNext(): void {
  if (revealed.value >= sections.value.length) return
  revealed.value += 1
}

/** Collapse back to just 错在哪 — for re-reading without the answer. */
function restart(): void {
  revealed.value = 1
}
</script>

<template>
  <div class="feedback">
    <div class="analysis-head">
      <span class="analysis-badge">AI 诊断</span>
      <span v-if="errorTypeDescription" class="analysis-cause">
        {{ errorTypeDescription }}
      </span>
      <!--
        The raw label stays available but demoted to a tooltip: it is the
        teacher/statistics identifier, not something a student should have
        to read.
      -->
      <code
        v-if="errorType"
        class="analysis-type"
        :title="`错因分类：${errorType}`"
      >
        {{ errorType }}
      </code>
    </div>

    <div
      v-for="section in sections"
      v-show="isRevealed(section)"
      :key="section"
      class="feedback-section"
    >
      <span class="feedback-title">{{ FEEDBACK_SECTION_TITLES[section] }}</span>
      <p class="feedback-prose">{{ prose[section] }}</p>
    </div>

    <div class="feedback-actions">
      <button
        v-if="!fullyRevealed"
        class="btn btn-reveal"
        type="button"
        @click="revealNext"
      >
        {{ revealLabel }}
      </button>
      <template v-else>
        <span class="feedback-done-note">
          以上是这道题的全部反馈。照着改一遍，再提交一次。
        </span>
        <button
          v-if="sections.length > 1"
          class="btn btn-again"
          type="button"
          title="收起提示和解答，回到只看错因"
          @click="restart"
        >
          重新自己想
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.feedback {
  padding: 10px 12px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.analysis-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.analysis-badge {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-accent);
  border: 1px solid var(--af-accent);
  border-radius: var(--af-radius-sm);
}

.analysis-type {
  font-size: 12px;
  color: var(--af-text-secondary);
}

/*
 * The Chinese reading of the error label. This is the one the student is
 * meant to read; `.analysis-type` beside it is the raw identifier, kept
 * small and dim so it reads as metadata rather than as the answer.
 */
.analysis-cause {
  font-size: 12px;
  color: var(--af-text-secondary);
}

/*
 * A released section. The left border is what makes the three layers
 * legible as one stack of cards rather than as three paragraphs that
 * happen to be near each other — and it gives the eye a place to stop
 * when a new layer appears below the fold.
 */
.feedback-section {
  margin-top: 10px;
  padding-left: 10px;
  border-left: 2px solid var(--af-border);
}

.feedback-title {
  display: block;
  margin-bottom: 2px;
  font-size: 11px;
  color: var(--af-text-muted);
}

.feedback-prose {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
  /* Model prose contains its own newlines; keep them. */
  white-space: pre-wrap;
  word-break: break-word;
}

.feedback-actions {
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

/*
 * 还是不会 — the gate. Outlined rather than filled, on purpose: a filled
 * button asks to be pressed, and this one is supposed to be the harder
 * choice than re-reading the code.
 */
.btn-reveal {
  color: var(--af-accent);
  background: transparent;
  border-color: var(--af-accent);
}

.btn-reveal:hover:not(:disabled) {
  background: var(--af-accent-subtle);
}

.feedback-done-note {
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
