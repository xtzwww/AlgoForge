<script setup lang="ts">
/**
 * The practice problems S7's 变式题 agent wrote from this mistake.
 *
 * Takes a list rather than one problem because that is what the column
 * holds: today the team produces one, but a client that iterates does not
 * need changing when a later phase asks for more. Several problems share
 * one heading and one disclosure control — "举一反三" is one invitation,
 * not one per problem.
 *
 * **Folded by default, and the reason is not tidiness.** Each variant
 * carries `difference`: what was changed from the problem the student just
 * failed. Read literally, that sentence usually names the mistake — "把 n
 * 从第二行挪到第一行" *is* the diagnosis. Rendering it open would hand
 * over the answer in the guise of extra practice, and would do it on the
 * screen where the student is still meant to be fixing the original. So
 * the block opens on request, and the closed state says what it is
 * waiting for.
 *
 * Like the other two feedback components, progress is not persisted and a
 * new set of variants closes again: the student re-reading yesterday's
 * mistake should not be handed today's spoiler by default.
 */
import { ref, watch } from 'vue'
import type { Variant } from '../api/mistake_book'

const props = defineProps<{
  /** The variants to offer. An empty list renders nothing at all. */
  variants: Variant[]
}>()

const expanded = ref(false)

/**
 * A different set closes again.
 *
 * Keyed on the array rather than on its contents: the sidecar sends a
 * fresh array per analysis, so identity is the honest signal — comparing
 * the statements would miss a second analysis that produced the same
 * variant for a different mistake.
 */
watch(
  () => props.variants,
  () => {
    expanded.value = false
  }
)

function toggle(): void {
  expanded.value = !expanded.value
}
</script>

<template>
  <div v-if="variants.length" class="variants">
    <div class="variants-head">
      <span class="variants-badge">举一反三</span>
      <span class="variants-count">{{ variants.length }} 道</span>
    </div>

    <!--
      What the closed state is waiting for, stated rather than left as a
      collapsed panel the student has to guess the contents of. This is
      also the only place that explains *why* it is closed.
    -->
    <p v-if="!expanded" class="variants-note">先把这道题改对，再看这些类似的题。</p>

    <button class="btn btn-variant" type="button" @click="toggle">
      {{ expanded ? '收起' : '看看类似的题' }}
    </button>

    <div v-show="expanded" class="variants-body">
      <div v-for="(v, i) in variants" :key="i" class="variant-card">
        <!-- 题干。模型给的正文，保留它自己的换行。 -->
        <p class="variant-statement">{{ v.statement }}</p>

        <div class="variant-samples">
          <div class="variant-sample">
            <span class="variant-sample-key">输入</span>
            <pre class="variant-sample-body">{{ v.sample_input }}</pre>
          </div>
          <div class="variant-sample">
            <span class="variant-sample-key">输出</span>
            <pre class="variant-sample-body">{{ v.sample_output }}</pre>
          </div>
        </div>

        <div v-if="v.knowledge_points.length" class="variant-points">
          <span v-for="p in v.knowledge_points" :key="p" class="variant-point">
            {{ p }}
          </span>
        </div>

        <!--
          The spoiler, inside the fold. Labelled as a difference rather
          than dropped in as prose: the student is meant to read it as
          "this is the knob that was turned", and unlabelled it reads as
          part of the new problem's statement.
        -->
        <p v-if="v.difference" class="variant-diff">
          <span class="variant-diff-key">与原题的区别</span>{{ v.difference }}
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.variants {
  padding: 10px 12px;
  background: var(--af-surface);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
}

.variants-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.variants-badge {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-accent);
  border: 1px solid var(--af-accent);
  border-radius: var(--af-radius-sm);
}

.variants-count {
  font-size: 12px;
  color: var(--af-text-muted);
}

.variants-note {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--af-text-muted);
}

.btn {
  padding: 4px 12px;
  margin-top: 10px;
  font-family: inherit;
  font-size: 12px;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    opacity var(--af-duration-fast) var(--af-ease);
}

.btn-variant {
  color: var(--af-accent);
  background: transparent;
  border-color: var(--af-accent);
}

.btn-variant:hover:not(:disabled) {
  background: var(--af-accent-subtle);
}

.variants-body {
  margin-top: 10px;
}

.variant-card {
  padding-left: 10px;
  border-left: 2px solid var(--af-border);
}

/* More than one variant is possible; the second needs air above it. */
.variant-card + .variant-card {
  margin-top: 12px;
}

.variant-statement {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.variant-samples {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
}

.variant-sample {
  flex: 1 1 160px;
}

.variant-sample-key {
  display: block;
  margin-bottom: 2px;
  font-size: 11px;
  color: var(--af-text-muted);
}

.variant-sample-body {
  margin: 0;
  padding: 6px 8px;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-secondary);
  background: var(--af-bg);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  /* Samples are whitespace-significant: trailing newlines matter. */
  white-space: pre-wrap;
  overflow-x: auto;
}

.variant-points {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.variant-point {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--af-text-secondary);
  background: var(--af-surface);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
}

.variant-diff {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

.variant-diff-key {
  margin-right: 6px;
  font-size: 11px;
  color: var(--af-text-muted);
}
</style>
