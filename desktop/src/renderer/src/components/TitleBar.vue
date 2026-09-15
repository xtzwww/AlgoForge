<script setup lang="ts">
/**
 * TitleBar — the app's own top strip (PU-6).
 *
 * Electron keeps its native window chrome, so this bar has nothing to
 * say there and hides itself when it would be empty (see the
 * `has-brand` rule in the stylesheet). In the browser there is no OS
 * chrome at all, so the bar carries the brand and the environment
 * badge — without it a web tab would be a borderless page with no
 * indication of what it is or which shell you are in.
 *
 * It also owns the drawer toggle. The toggle is only visible below
 * 1024px, but it has to live somewhere that exists in *both* shells,
 * because an Electron window can be narrowed past that breakpoint too.
 * Putting it here rather than in the sidebar avoids a control that
 * vanishes along with the thing it opens.
 *
 * The environment badge is where {@link RuntimeCapabilities} reaches the
 * UI. It reports what this shell cannot do and why, which is the honest
 * version of "disable the control and explain": the web target has no
 * native file access today and no feature needs it yet, so rather than
 * ship a dead button this states the limitation once, in the one place
 * a user would look for it. When a real export-style control arrives it
 * should follow the same shape — disabled, with the reason from
 * `runtime.capabilities`, not silently absent.
 */

import { computed } from 'vue'
import { runtime } from '../runtime'

const props = defineProps<{
  /** Drawer state, so the toggle can report it to assistive tech. */
  drawerOpen?: boolean
}>()

const emit = defineEmits<{ (e: 'toggle-drawer'): void }>()

/**
 * Only the browser shell needs a brand bar; Electron's window already
 * shows the title. Drives the `has-brand` class the stylesheet uses to
 * decide whether the bar occupies any space.
 */
const isWeb = computed<boolean>(() => runtime.kind === 'web')

/**
 * What this shell cannot do, phrased for a user rather than a
 * developer. Built from the capability flags so adding one to the
 * runtime forces a decision here about how to describe it.
 */
const limitations = computed<string[]>(() => {
  const notes: string[] = []
  if (!runtime.capabilities.nativeFileAccess) {
    notes.push('无法直接读写本机文件（浏览器只能下载）')
  }
  if (!runtime.capabilities.windowReveal) {
    notes.push('页面加载即显示，没有启动遮罩')
  }
  return notes
})

/** Tooltip for the badge: identity, plus the limitations if any. */
const badgeTitle = computed<string>(() => {
  if (limitations.value.length === 0) {
    return `${runtime.displayName}：功能完整`
  }
  return `${runtime.displayName}，以下功能不可用：\n· ${limitations.value.join('\n· ')}`
})
</script>

<template>
  <header class="title-bar" :class="{ 'has-brand': isWeb, 'drawer-open': props.drawerOpen }">
    <button
      class="drawer-toggle"
      :aria-expanded="props.drawerOpen ? 'true' : 'false'"
      aria-label="切换导航菜单"
      @click="emit('toggle-drawer')"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        aria-hidden="true"
      >
        <path d="M4 7h16M4 12h16M4 17h16" />
      </svg>
    </button>

    <template v-if="isWeb">
      <svg
        class="brand-mark"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M4 18 12 5l8 13" />
        <path d="M8.5 15h7" />
      </svg>
      <span class="brand-name">AlgoForge</span>
      <span class="env-badge" :title="badgeTitle">{{ runtime.displayName }}</span>
    </template>
  </header>
</template>

<style scoped>
.title-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  height: var(--spacing-topbar);
  padding: 0 12px;
  background: var(--af-surface);
  border-bottom: 1px solid var(--af-border);
}

/* Electron at ≥ 1024px has native chrome and no drawer toggle to show,
 * so the bar would be an empty 48px strip. Hide it and let the content
 * start at the top. */
@media (min-width: 1024px) {
  .title-bar:not(.has-brand) {
    display: none;
  }
}

.drawer-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  color: var(--af-text-secondary);
  background: transparent;
  border: none;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
}

.drawer-toggle svg {
  width: 20px;
  height: 20px;
}

.drawer-toggle:hover {
  color: var(--af-text-primary);
  background: var(--af-elevated);
}

/* The drawer only exists below this breakpoint, so the toggle that
 * opens it is meaningless above it. */
@media (min-width: 1024px) {
  .drawer-toggle {
    display: none;
  }
}

.brand-mark {
  width: 22px;
  height: 22px;
  color: var(--af-accent);
}

.brand-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--af-text-primary);
  letter-spacing: 0.01em;
}

.env-badge {
  margin-left: auto;
  padding: 2px 10px;
  font-size: 11px;
  color: var(--af-text-secondary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: 999px;
  cursor: help;
  white-space: nowrap;
}
</style>
