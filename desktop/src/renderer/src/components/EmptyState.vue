<script setup lang="ts">
/**
 * EmptyState — the shared "there is nothing here, and here is why" panel (PU-9).
 *
 * **Why this is a component.** PU-9's brief says the three pages must not
 * render blank when they have no data, and "must not render blank" is not
 * the same requirement as "must render a sentence". The pre-PU-9 pages
 * each had a grey one-liner that stated the absence and stopped there:
 *
 *   > 错题本为空。判题失败后，可在练习页面将错题加入错题本。
 *
 * That tells the user what they are missing and leaves them to work out
 * the route to it. This component is shaped around the fix — a `title`,
 * an `explanation` of how the page came to be empty, and an optional
 * actionable `action` — so an empty page always ends in a next step.
 *
 * **Why the button is a `<button>` emitting `navigate`, not a
 * `<RouterLink>`.** These pages are used in both shells and the same
 * destination sometimes has no route of its own: "去练习题" is a different
 * route from free-run mode, so the *page* decides where to go and this
 * component only reports that the user asked to go there. That also keeps
 * the component renderable in a test without mounting a router.
 *
 * **One component, not three near-copies.** The failure mode of copying
 * an empty state is that the three copies drift on the thing that matters
 * most — whether they offer a way out at all — because nobody diffs a
 * grey paragraph. A single component makes "has an action" a property of
 * the empty state rather than a property of the page that remembered.
 */

const props = withDefaults(
  defineProps<{
    /** Bold first line. Says what is missing, not that something failed. */
    title: string
    /**
     * Optional second paragraph. Use it to explain *how* the page got
     * here when that is not obvious (e.g. "only wrong submissions are
     * recorded"), so the emptiness does not read as a bug.
     */
    explanation?: string
    /** Label for the optional call-to-action button. */
    actionLabel?: string
    /**
     * A recognised state needing one step to resolve (e.g. "no rows yet")
     * reads as an invitation. `search` is for "your filters matched
     * nothing", which is a different situation: the user caused it, the
     * fix is to clear a filter, and the tone should be flatter rather
     * than welcoming.
     */
    variant?: 'first-run' | 'search'
    /** A single glyph drawn above the title; see `ICONS`. */
    icon?: 'book' | 'chart' | 'calendar' | 'search'
  }>(),
  { variant: 'first-run', explanation: '', actionLabel: '', icon: undefined }
)

const emit = defineEmits<{ (e: 'navigate'): void }>()

/**
 * Inline SVG paths on a 24×24 grid. AGENTS.md forbids npm component
 * libraries and four decorative glyphs do not justify one; they are also
 * `aria-hidden` because the title beside them already carries the meaning.
 */
const ICONS: Record<string, string> = {
  book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z',
  chart: 'M3 3v18h18M7 15l4-5 3 3 5-7',
  calendar:
    'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35'
}
</script>

<template>
  <div class="empty-state" :class="`empty-${props.variant}`">
    <svg
      v-if="props.icon"
      class="empty-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path :d="ICONS[props.icon]" />
    </svg>

    <p class="empty-title">{{ props.title }}</p>
    <p v-if="props.explanation" class="empty-explanation">{{ props.explanation }}</p>

    <button v-if="props.actionLabel" class="empty-action" type="button" @click="emit('navigate')">
      {{ props.actionLabel }}
      <span class="empty-arrow" aria-hidden="true">→</span>
    </button>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  /* The panel is centred in whatever is left of the page rather than
   * pinned under the header: an empty page that hugs the top looks like
   * content that failed to load. */
  justify-content: center;
  gap: 8px;
  min-height: 240px;
  padding: 40px 24px;
  text-align: center;
  background: var(--af-surface);
  border: 1px dashed var(--af-border-strong);
  border-radius: var(--af-radius-md);
}

.empty-search {
  /* A filtered-to-nothing list is a passing state, not a destination —
   * the dashed border and the tall minimum read as "something goes here"
   * and would overstate it. */
  min-height: 160px;
  background: transparent;
  border-style: solid;
  border-color: var(--af-border);
}

.empty-icon {
  width: 32px;
  height: 32px;
  margin-bottom: 4px;
  color: var(--af-text-muted);
}

.empty-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--af-text-primary);
}

.empty-explanation {
  max-width: 46ch;
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--af-text-secondary);
}

.empty-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 6px 16px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
  background: var(--af-accent);
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition: background var(--af-duration-fast) var(--af-ease);
}

.empty-action:hover {
  background: var(--af-accent-hover);
}

.empty-arrow {
  transition: transform var(--af-duration-fast) var(--af-ease);
}

.empty-action:hover .empty-arrow {
  transform: translateX(2px);
}

@media (prefers-reduced-motion: reduce) {
  .empty-arrow,
  .empty-action {
    transition: none;
  }

  .empty-action:hover .empty-arrow {
    transform: none;
  }
}
</style>
