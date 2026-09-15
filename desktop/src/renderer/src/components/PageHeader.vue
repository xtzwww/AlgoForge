<script setup lang="ts">
/**
 * PageHeader — the title/actions bar shared by the three list-shaped pages (PU-9).
 *
 * The mistake book, the profile and the review plan had each grown their
 * own copy of the same three things: a 22px `<h1>`, a right-aligned
 * refresh button with a `loading ? '加载中...' : '刷新'` label, and a red
 * error box underneath. Three copies of a header is three places to
 * change the heading size, and the copies had already drifted on
 * details that matter — the review plan's header had a 12px bottom
 * margin where the other two had 16px, and only the profile view kept
 * its summary row *outside* the header, so the three pages' content
 * started at three different heights.
 *
 * **What it does not own.** The refresh button's *state*, the error's
 * *text* and the page's *data* all stay with the page — this component
 * receives a boolean and a string. It is a layout component, not a
 * container: a page with no refresh (a static page) simply omits `loading`,
 * and then no button is rendered rather than a disabled one.
 *
 * **The error is part of the header on purpose.** "请求失败" describes the
 * whole page's state, not one region's, and putting it in the header is
 * what guarantees it is visible whatever branch the body took — the
 * pre-PU-9 pages rendered it above the list, so a page that had switched
 * to its empty state showed the failure *and* "暂无数据" as if the second
 * explained the first.
 */

import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    /** Optional line under the title: counts, a subtitle, a time range. */
    subtitle?: string
    /**
     * Omit entirely (`undefined`) for a page with nothing to refresh —
     * that renders no button, which is not the same as a disabled one.
     */
    loading?: boolean
    /** Refresh button label. Defaults to 刷新 / 加载中… */
    refreshLabel?: string
    /** Non-empty renders the error banner. */
    error?: string
    /**
     * Error banner prefix. Defaults to 请求失败; a page whose failure has
     * a more specific subject (e.g. 无法加载题库) passes its own.
     */
    errorPrefix?: string
  }>(),
  {
    subtitle: '',
    loading: undefined,
    refreshLabel: '',
    error: '',
    errorPrefix: '请求失败'
  }
)

const emit = defineEmits<{ (e: 'refresh'): void }>()

/**
 * Whether to render the refresh control at all.
 *
 * A `computed`, not a plain const: `props` is reactive, and although
 * `loading` never switches between `undefined` and a boolean in the
 * current callers, a plain read here would freeze the decision at setup
 * time and quietly stop tracking the prop. The cost of the computed is
 * zero and the failure mode it avoids is invisible.
 */
const hasRefresh = computed<boolean>(() => props.loading !== undefined)

const refreshText = computed<string>(() => {
  if (props.refreshLabel) return props.refreshLabel
  return props.loading ? '加载中…' : '刷新'
})
</script>

<template>
  <header class="page-header">
    <div class="page-heading">
      <h1>
        {{ props.title }}
        <!-- A count that belongs to the title rather than to the body:
             it describes the page, and it must survive the empty state. -->
        <slot name="count" />
      </h1>
      <p v-if="props.subtitle" class="page-subtitle">{{ props.subtitle }}</p>
    </div>

    <div class="page-actions">
      <slot name="actions" />
      <button
        v-if="hasRefresh"
        class="refresh-btn"
        type="button"
        :disabled="props.loading"
        @click="emit('refresh')"
      >
        {{ refreshText }}
      </button>
    </div>
  </header>

  <p v-if="props.error" class="page-error">{{ props.errorPrefix }}：{{ props.error }}</p>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}

.page-heading {
  min-width: 0;
}

.page-header h1 {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.page-subtitle {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: var(--af-text-secondary);
}

.page-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.refresh-btn {
  padding: 5px 14px;
  font-family: inherit;
  font-size: 13px;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  white-space: nowrap;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    color var(--af-duration-fast) var(--af-ease);
}

.refresh-btn:hover:not(:disabled) {
  background: var(--af-overlay);
}

.refresh-btn:disabled {
  color: var(--af-text-muted);
  border-color: var(--af-border);
  cursor: not-allowed;
}

.page-error {
  padding: 8px 12px;
  margin: 0 0 12px 0;
  font-size: 13px;
  color: var(--af-text-primary);
  background: color-mix(in srgb, var(--af-danger) 18%, transparent);
  border: 1px solid color-mix(in srgb, var(--af-danger) 45%, transparent);
  border-radius: var(--af-radius-sm);
}
</style>
