<script setup lang="ts">
/**
 * VerdictBadge — the one place a judge verdict becomes a colour (PU-8).
 *
 * Before this component, `OutputPanel.vue` carried a private
 * `statusColor()` mapping AC→#22c55e, WA→#ef4444 and so on. PU-8 needed
 * the same badge in the output panel, the case list and (PU-9) the
 * mistake book and submission history — four copies of a switch that is
 * contractually bound to the backend's `JudgeStatus` enum. A missed
 * branch in one copy would show the wrong colour for one verdict on one
 * screen, which is exactly the kind of drift a shared component exists to
 * prevent.
 *
 * **The palette comes from PU-0, not from here.** Every colour is a
 * `var(--af-verdict-*)` reference, so retheming is a `tokens.css` edit
 * and this file never holds a hex literal. The `@theme` block mirrors
 * the same values as `--color-af-verdict-*` for utility use; the
 * `:root` block's `--af-verdict-*` is what a `var()` inside `<style
 * scoped>` must name. Getting that pair backwards fails silently — see
 * the tokens file's header.
 *
 * **Unknown verdicts degrade, they do not throw.** The status arrives as
 * a plain string from the WebSocket, and a future backend verdict (or a
 * typo) must render as a neutral grey rather than a blank badge or a
 * crash. `--af-verdict-unknown` is that fallback, and the raw string is
 * still displayed so the value is never swallowed.
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** Verdict as the sidecar spells it: `AC` / `WA` / `TLE` / … */
    status: string
    /** Renders at three sizes; `md` is the inline default. */
    size?: 'sm' | 'md' | 'lg'
    /**
     * Show the long Chinese name beside the short code, e.g.
     * `AC 通过`. Useful where the code alone is not self-explanatory.
     */
    withLabel?: boolean
  }>(),
  { size: 'md', withLabel: false }
)

/**
 * The verdicts the backend defines, in `JudgeStatus` order.
 *
 * Kept as a list of pairs rather than a lookup object so the label and
 * the CSS variable are declared together — a `Record<string, string>`
 * would let a verdict pick up a colour with no label, or the reverse.
 */
const VERDICTS: { code: string; label: string; cssVar: string }[] = [
  { code: 'AC', label: '通过', cssVar: '--af-verdict-ac' },
  { code: 'WA', label: '答案错误', cssVar: '--af-verdict-wa' },
  { code: 'TLE', label: '运行超时', cssVar: '--af-verdict-tle' },
  { code: 'MLE', label: '内存超限', cssVar: '--af-verdict-mle' },
  { code: 'CE', label: '编译错误', cssVar: '--af-verdict-ce' },
  { code: 'RE', label: '运行错误', cssVar: '--af-verdict-re' }
]

const known = computed(() => VERDICTS.find((v) => v.code === props.status) ?? null)

/** Falls back to the neutral token so an unknown code is still visible. */
const colorVar = computed<string>(() => `var(${known.value?.cssVar ?? '--af-verdict-unknown'})`)

/**
 * The badge's tint. `color-mix` is used rather than a second set of
 * hand-picked background tokens: the tint has to be derived from the
 * verdict colour or the two would drift apart the first time a colour is
 * retuned. Supported in the Chromium Electron ships with.
 */
const backgroundVar = computed<string>(
  () => `color-mix(in srgb, ${colorVar.value} 18%, transparent)`
)

const borderVar = computed<string>(() => `color-mix(in srgb, ${colorVar.value} 40%, transparent)`)

const label = computed<string>(() => known.value?.label ?? '未知结果')
</script>

<template>
  <span
    class="verdict-badge"
    :class="`verdict-${props.size}`"
    :style="{ color: colorVar, background: backgroundVar, borderColor: borderVar }"
    :title="`${props.status} · ${label}`"
  >
    <span class="verdict-code">{{ props.status }}</span>
    <span v-if="props.withLabel" class="verdict-label">{{ label }}</span>
  </span>
</template>

<style scoped>
/*
 * Only the layout lives here. The three colours are inline styles bound
 * to the computed CSS variables above, because they depend on the
 * verdict at runtime — a static class per verdict would mean a sixth
 * place to add a verdict when the backend grows one.
 */
.verdict-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  font-weight: 600;
  white-space: nowrap;
  /* Tabular figures stop the badge changing width between `AC` and
   * `WA` when it sits in a column of verdicts. */
  font-variant-numeric: tabular-nums;
}

.verdict-sm {
  padding: 0 6px;
  font-size: 11px;
  line-height: 18px;
}

.verdict-md {
  padding: 1px 8px;
  font-size: 13px;
  line-height: 20px;
}

.verdict-lg {
  padding: 4px 14px;
  font-size: 24px;
  line-height: 1.2;
  border-radius: var(--af-radius-md);
}

.verdict-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.verdict-label {
  font-weight: 400;
  opacity: 0.85;
}
</style>
