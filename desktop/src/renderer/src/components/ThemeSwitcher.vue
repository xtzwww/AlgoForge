<script setup lang="ts">
/**
 * ThemeSwitcher — the visual-style control in the topbar's left corner.
 *
 * Why a menu and not a cycle button
 * ---------------------------------
 * A cycle button is one click and no reading, which is right for
 * light/dark and wrong for this: the three themes are not points on one
 * axis, they are three different documents. "赛制终端" and "纸感学术" do
 * not mean anything until you have seen them, so each entry carries a
 * line saying what it is *for* — a projector in a bright room is a
 * different situation from a contest. A cycle button cannot show that
 * without becoming a menu anyway.
 *
 * Why the menu closes on outside click *and* Escape
 * -------------------------------------------------
 * Escape is the keyboard path; outside click is the mouse path. A
 * dropdown that only handles one is a dropdown that traps the other half
 * of its users, and this one sits next to the breadcrumbs on every page,
 * so it will be opened by accident fairly often.
 *
 * Why it is a control and not a preference page
 * ---------------------------------------------
 * It has to be reachable in one click: the point of offering themes is
 * that one of them is better for the room you are currently in, and a
 * setting two screens deep would only be found by someone who already
 * knows it exists.
 */

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { THEMES, setTheme, theme, themeOption, type ThemeId } from '../composables/useTheme'

const open = ref(false)
const root = ref<HTMLElement | null>(null)

/** The label shown next to the icon. Hidden below 1024px by CSS. */
const currentLabel = computed<string>(() => themeOption.value.label)
const currentId = computed<ThemeId>(() => theme.value)

function toggle(): void {
  open.value = !open.value
}

function pick(id: ThemeId): void {
  setTheme(id)
  open.value = false
}

/**
 * Close on a click anywhere outside.
 *
 * Bound on `document` rather than on the root element because the menu
 * is absolutely positioned and a click on the page lands on whatever is
 * under it, not on this component's subtree.
 */
function onDocumentPointerDown(event: MouseEvent): void {
  if (!open.value) return
  if (root.value?.contains(event.target as Node)) return
  open.value = false
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && open.value) {
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentPointerDown)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div ref="root" class="theme-switcher">
    <button
      class="af-press theme-trigger"
      type="button"
      :aria-expanded="open ? 'true' : 'false'"
      aria-haspopup="menu"
      :title="`画面风格：${currentLabel}（点击切换）`"
      @click="toggle"
    >
      <svg
        class="theme-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <!-- A palette: three dots over a dish — the one shape that says
             "colour" without saying "settings". -->
        <path d="M12 21a9 9 0 1 1 9-9c0 2-1.5 2-2.5 2H16a2 2 0 0 1-2-2v-1a2 2 0 0 0-4 0" />
        <circle cx="7.5" cy="11.5" r="0.9" fill="currentColor" stroke="none" />
        <circle cx="10" cy="7.5" r="0.9" fill="currentColor" stroke="none" />
        <circle cx="15" cy="8" r="0.9" fill="currentColor" stroke="none" />
      </svg>
      <span class="theme-label">{{ currentLabel }}</span>
      <svg
        class="theme-caret"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>

    <ul v-if="open" class="theme-menu" role="menu">
      <li v-for="option in THEMES" :key="option.id" role="none">
        <button
          class="theme-item"
          :class="{ 'is-active': option.id === currentId }"
          type="button"
          role="menuitemradio"
          :aria-checked="option.id === currentId ? 'true' : 'false'"
          @click="pick(option.id)"
        >
          <span class="theme-item-head">
            <span class="theme-item-label">{{ option.label }}</span>
            <span v-if="option.id === currentId" class="theme-item-tick" aria-hidden="true">✓</span>
          </span>
          <span class="theme-item-hint">{{ option.hint }}</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.theme-switcher {
  position: relative;
  flex-shrink: 0;
}

.theme-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 8px;
  font-family: inherit;
  font-size: 12px;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border-strong);
  border-radius: 999px;
  cursor: pointer;
  transition:
    color var(--af-duration-fast) var(--af-ease),
    background var(--af-duration-fast) var(--af-ease),
    border-color var(--af-duration-fast) var(--af-ease),
    box-shadow var(--af-duration-fast) var(--af-ease);
}

.theme-trigger:hover {
  color: var(--af-text-primary);
  background: var(--af-overlay);
  border-color: var(--af-accent);
}

.theme-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--af-accent);
}

/* Below 1024px the topbar is crowded by the drawer toggle, the brand and
 * the breadcrumbs. The icon and the caret are enough to find it, and the
 * `title` still names the current theme. */
.theme-label {
  white-space: nowrap;
}

.theme-caret {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  opacity: 0.7;
}

@media (max-width: 1023px) {
  .theme-label {
    display: none;
  }
}

.theme-menu {
  position: absolute;
  z-index: 30;
  top: calc(100% + 6px);
  left: 0;
  min-width: 268px;
  margin: 0;
  padding: 5px;
  list-style: none;
  background: var(--af-glass-elevated, var(--af-elevated));
  -webkit-backdrop-filter: var(--af-glass-blur, none);
  backdrop-filter: var(--af-glass-blur, none);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-md);
  box-shadow: var(--af-shadow-md);
}

.theme-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 7px 9px;
  font-family: inherit;
  text-align: left;
  color: var(--af-text-secondary);
  background: transparent;
  border: none;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background var(--af-duration-fast) var(--af-ease),
    color var(--af-duration-fast) var(--af-ease);
}

.theme-item:hover {
  color: var(--af-text-primary);
  background: var(--af-border);
}

.theme-item.is-active {
  color: var(--af-accent);
}

.theme-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.theme-item-label {
  font-size: 13px;
  font-weight: 500;
}

.theme-item-tick {
  font-size: 12px;
}

/* The reason this is a menu and not a cycle button: the hint is what
 * tells you which theme fits the room you are in. */
.theme-item-hint {
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

@media (prefers-reduced-motion: reduce) {
  .theme-trigger,
  .theme-item {
    transition: none;
  }
}
</style>
