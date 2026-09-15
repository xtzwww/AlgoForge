<script lang="ts">
/**
 * The nav item shape, exported from a plain `<script>` block because
 * `<script setup>` cannot contain exports. App.vue owns the actual list
 * — navigation is the shell's business, not the sidebar's.
 */
export type NavItem = {
  id: string
  label: string
  /** SVG path data, drawn on a 24×24 grid. */
  path: string
}
</script>

<script setup lang="ts">
/**
 * AppSidebar — primary navigation (PU-6).
 *
 * Three presentations, all driven by CSS media queries rather than by a
 * resize listener:
 *
 *   ≥ 1280px    expanded   icon + label
 *   1024–1280px collapsed  icon only, label in the `title` tooltip
 *   < 1024px    drawer     off-canvas, slid in by the title bar's toggle
 *
 * Doing the first two in CSS keeps the component free of viewport state
 * that could disagree with what is actually on screen. The drawer needs
 * a little JavaScript, but only for *whether it is open* — whether it is
 * a drawer at all is still decided by the media query, so a window
 * resized past the breakpoint cannot leave a stale open drawer behind.
 *
 * Icons are inline SVG. AGENTS.md forbids npm component libraries, and
 * five glyphs are not worth a dependency; an icon font or sprite would
 * add a network/asset concern for less clarity than the paths below.
 *
 * PU-7 replaces this with the router-aware version (breadcrumbs, an
 * env-check status footer). What is here is the responsive baseline it
 * builds on, deliberately without those.
 */

const props = defineProps<{
  items: NavItem[]
  active: string
  /** Drawer visibility. Ignored by CSS at ≥ 1024px. */
  drawerOpen?: boolean
}>()

const emit = defineEmits<{ (e: 'navigate', id: string): void }>()

function select(id: string): void {
  emit('navigate', id)
}

/**
 * PU-0's Tailwind probe, re-homed.
 *
 * The old tab bar carried a `bg-af-accent-subtle` badge whose only job was
 * to be visibly styled or visibly not: if Tailwind's `@import` chain in
 * main.css ever broke, the badge would lose its background and rounding and
 * say so. PU-6 deleted the tab bar, and with it the *only* Tailwind utility
 * in the frontend — everything else is scoped CSS reading `--af-*` custom
 * properties, which works whether or not Tailwind is generating utilities.
 *
 * So the probe moves here rather than disappearing. It stays in the source
 * unconditionally, which is what matters: Tailwind scans files as text, so
 * the classes keep being generated and `grep bg-af-accent-subtle` on a build
 * output still answers the question. Only the rendering is dev-gated, since
 * it is a wiring check and not product UI.
 */
const showProbe = import.meta.env.DEV
</script>

<template>
  <nav class="sidebar" :class="{ 'drawer-open': props.drawerOpen }">
    <ul class="nav-list">
      <li v-for="item in props.items" :key="item.id">
        <button
          class="nav-item"
          :class="{ active: item.id === props.active }"
          :title="item.label"
          :aria-current="item.id === props.active ? 'page' : undefined"
          @click="select(item.id)"
        >
          <svg
            class="nav-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path :d="item.path" />
          </svg>
          <span class="nav-label">{{ item.label }}</span>
        </button>
      </li>
    </ul>

    <!-- See `showProbe` above: a Tailwind wiring check, not product UI. -->
    <div v-if="showProbe" class="probe">
      <span class="bg-af-accent-subtle text-af-accent rounded-full px-3 py-1 text-xs">
        Tailwind v4 ✓
      </span>
    </div>
  </nav>
</template>

<style scoped>
/* The sidebar is a flex column so a future footer (PU-7's env-check
 * indicator) has somewhere to sit without restructuring. */
.sidebar {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: var(--spacing-sidebar);
  background: var(--af-surface);
  border-right: 1px solid var(--af-border);
  overflow-y: auto;
  overflow-x: hidden;
  transition: width var(--af-duration-base) var(--af-ease);
}

.nav-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
  font-size: 14px;
  font-family: inherit;
  text-align: left;
  color: var(--af-text-secondary);
  background: transparent;
  border: none;
  /* The active marker is a left bar rather than a filled block, so the
   * current page reads as marked instead of as a second button. */
  border-left: 3px solid transparent;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  white-space: nowrap;
  transition:
    color var(--af-duration-fast) var(--af-ease),
    background var(--af-duration-fast) var(--af-ease);
}

.nav-item:hover {
  color: var(--af-text-primary);
  background: var(--af-elevated);
}

.nav-item.active {
  color: var(--af-text-primary);
  background: var(--af-accent-subtle);
  border-left-color: var(--af-accent);
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
}

/* `margin-top: auto` parks the probe at the bottom of the column, which is
 * also where PU-7's env-check footer will go. */
.probe {
  margin-top: auto;
  padding: 8px;
}

/* ---- 1024–1280px: collapsed to icons ---- */
@media (max-width: 1279px) {
  .sidebar {
    width: var(--spacing-sidebar-collapsed);
  }

  .nav-item {
    justify-content: center;
    gap: 0;
    padding: 10px 0;
  }

  .nav-label {
    display: none;
  }

  /* 60px of sidebar cannot hold the badge; it would just clip. */
  .probe {
    display: none;
  }
}

/* ---- < 1024px: off-canvas drawer ----
 * `position: fixed` so it overlays the content instead of squeezing it;
 * at this width the content needs the whole viewport. The drawer is
 * hidden by translating it out rather than with `display: none`, so the
 * slide transition has something to animate.
 *
 * It starts below the title bar rather than at the top of the viewport.
 * The title bar is always present at this width (both shells), and if the
 * drawer covered it the scrim would sit over the hamburger, leaving the
 * control that opened the drawer unable to close it again. */
@media (max-width: 1023px) {
  .sidebar {
    position: fixed;
    top: var(--spacing-topbar);
    left: 0;
    bottom: 0;
    z-index: 40;
    width: var(--spacing-sidebar);
    transform: translateX(-100%);
    box-shadow: var(--af-shadow-lg);
  }

  .sidebar.drawer-open {
    transform: translateX(0);
  }

  /* Inside the drawer the full label is always shown, whatever the
   * collapsed rule above did. */
  .nav-item {
    justify-content: flex-start;
    gap: 12px;
    padding: 10px 12px;
  }

  .nav-label {
    display: block;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sidebar {
    transition: none;
  }
}
</style>
