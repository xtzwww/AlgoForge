<script lang="ts">
/**
 * The nav item shape, exported from a plain `<script>` block because
 * `<script setup>` cannot contain exports.
 */
export type NavItem = {
  /** Route name — also the `RouterLink` target and the icon key. */
  name: string
  /** Label, taken from the route's `meta.title`. */
  label: string
  /** SVG path data, drawn on a 24×24 grid. */
  path: string
}
</script>

<script setup lang="ts">
/**
 * AppSidebar — primary navigation, logo and environment status (PU-6,
 * rebuilt for routing in PU-7).
 *
 * Three presentations, all driven by CSS media queries rather than by a
 * resize listener:
 *
 *   ≥ 1280px    expanded   icon + label
 *   1024–1280px collapsed  icon only, label in the `title` tooltip
 *   < 1024px    drawer     off-canvas, slid in by the topbar's toggle
 *
 * Doing the first two in CSS keeps the component free of viewport state
 * that could disagree with what is actually on screen. The drawer needs
 * a little JavaScript, but only for *whether it is open* — whether it is
 * a drawer at all is still decided by the media query, so a window
 * resized past the breakpoint cannot leave a stale open drawer behind.
 *
 * **Styling split (PU-7).** The nav list, the logo and the status footer
 * are written as scoped CSS against the `--af-*` / `--spacing-*` tokens
 * rather than as Tailwind utilities, because every interesting rule here
 * is a *compound* one: the same element has to be positioned, sized and
 * highlighted differently depending on a breakpoint *and* on the drawer's
 * open state. Tailwind expresses the breakpoint half well and the state
 * half poorly — `.nav-item.router-link-active` is a selector, while two
 * conflicting utilities in one class list are resolved by stylesheet
 * order, not by class order, which is exactly the kind of mistake that
 * fails silently. Static layout that maps to single utilities *is* done
 * with Tailwind, in `AppTopbar.vue` and `App.vue`.
 *
 * Icons are inline SVG. AGENTS.md forbids npm component libraries, and
 * five glyphs are not worth a dependency; an icon font or sprite would
 * add a network/asset concern for less clarity than the paths below.
 *
 * Navigation is `<RouterLink>`, so the current page is the URL's job
 * rather than a prop's. The highlight uses RouterLink's own
 * `router-link-active`, which matches by path prefix — meaning 题库
 * stays highlighted while you are on `/problems/12`, which is the
 * behaviour you want from a section nav.
 */

import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { navRoutes } from '../router'
import { useSidecarStatus } from '../composables/useSidecarStatus'

defineProps<{
  /** Drawer visibility. Ignored by CSS at ≥ 1024px. */
  drawerOpen?: boolean
}>()

const emit = defineEmits<{ (e: 'navigate'): void }>()

/** Icon path per route name; see the component docstring for why inline. */
const ICONS: Record<string, string> = {
  practice: 'M8 18l-5-6 5-6M16 6l5 6-5 6',
  problems: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  mistakes:
    'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z',
  profile: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  plan: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z'
}

const items = computed<NavItem[]>(() =>
  navRoutes.map((r) => ({ name: r.name, label: r.title, path: ICONS[r.name] ?? '' }))
)

const { phase, errorMessage, retry } = useSidecarStatus()

/**
 * The footer indicator.
 *
 * `degraded` is not `failed` and must not look like it: the sidecar
 * answered, so nothing is broken and retrying cannot help — this machine
 * just has no usable toolchain. Showing a red dot there would send the
 * user chasing a problem that does not exist.
 */
const status = computed(() => {
  switch (phase.value) {
    case 'ready':
      return { tone: 'ok', label: '分析引擎就绪' }
    case 'degraded':
      return { tone: 'warn', label: '没有可用的语言工具链' }
    case 'failed':
      return { tone: 'error', label: '分析引擎未连接' }
    default:
      return { tone: 'idle', label: '正在检查环境…' }
  }
})

/** Full text for the tooltip: the label, plus the reason when there is one. */
const statusDetail = computed<string>(() =>
  phase.value === 'failed' && errorMessage.value
    ? `${status.value.label}\n${errorMessage.value}`
    : status.value.label
)

/** Only a real failure is worth a retry; `degraded` will not change. */
const canRetry = computed<boolean>(() => phase.value === 'failed')
</script>

<template>
  <nav class="sidebar" :class="{ 'drawer-open': drawerOpen }">
    <div class="brand">
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
    </div>

    <ul class="nav-list">
      <li v-for="item in items" :key="item.name">
        <RouterLink
          class="nav-item"
          :to="{ name: item.name }"
          :title="item.label"
          @click="emit('navigate')"
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
        </RouterLink>
      </li>
    </ul>

    <!--
      The environment footer PU-6's flex column was left room for. It reads
      the shared status rather than probing: the content area shows the
      same answer, and two probes could disagree.
    -->
    <div class="status" :class="`status-${status.tone}`" :title="statusDetail">
      <span class="status-dot" aria-hidden="true" />
      <span class="status-label">{{ status.label }}</span>
      <button v-if="canRetry" class="status-retry" type="button" @click="retry">重试</button>
    </div>
  </nav>
</template>

<style scoped>
/* A flex column: the nav list takes its natural height and the status
 * footer is pushed to the bottom by `margin-top: auto`. */
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

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  height: var(--spacing-topbar);
  padding: 0 16px;
  border-bottom: 1px solid var(--af-border);
}

.brand-mark {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  color: var(--af-accent);
}

.brand-name {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--af-text-primary);
  white-space: nowrap;
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
  text-align: left;
  text-decoration: none;
  color: var(--af-text-secondary);
  background: transparent;
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

/* A compound selector, which is the reason this component is not written
 * in utilities — see the docstring. RouterLink's default active class
 * matches by path prefix, so 题库 stays marked on /problems/12. */
.nav-item.router-link-active {
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

/* ---- Environment status footer ---- */
.status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  padding: 10px 16px;
  border-top: 1px solid var(--af-border);
  font-size: 12px;
  color: var(--af-text-muted);
}

.status-dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 999px;
  background: currentColor;
}

.status-ok {
  color: var(--af-success);
}

.status-warn {
  color: var(--af-warning);
}

.status-error {
  color: var(--af-danger);
}

/* The label is the informative part, so it keeps the muted text colour
 * and only the dot carries the status colour. */
.status-label {
  color: var(--af-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-retry {
  margin-left: auto;
  padding: 2px 8px;
  font-size: 11px;
  font-family: inherit;
  color: var(--af-text-primary);
  background: var(--af-elevated);
  border: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  white-space: nowrap;
}

.status-retry:hover {
  background: var(--af-overlay);
}

/* ---- 1024–1280px: collapsed to icons ---- */
@media (max-width: 1279px) {
  .sidebar {
    width: var(--spacing-sidebar-collapsed);
  }

  .brand {
    justify-content: center;
    padding: 0;
  }

  /* No room for the wordmark at 60px; the mark alone still identifies
   * the app, and the window title carries the name. */
  .brand-name {
    display: none;
  }

  .nav-item {
    justify-content: center;
    gap: 0;
    padding: 10px 0;
  }

  .nav-label {
    display: none;
  }

  /* The dot alone is the indicator at this width; the tooltip keeps the
   * text reachable. */
  .status {
    justify-content: center;
    padding: 10px 0;
  }

  .status-label,
  .status-retry {
    display: none;
  }
}

/* ---- < 1024px: off-canvas drawer ----
 * `position: fixed` so it overlays the content instead of squeezing it;
 * at this width the content needs the whole viewport. The drawer is
 * hidden by translating it out rather than with `display: none`, so the
 * slide transition has something to animate.
 *
 * It starts below the topbar rather than at the top of the viewport.
 * The topbar is always present at this width (both shells), and if the
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

  .brand {
    justify-content: flex-start;
    padding: 0 16px;
  }

  .brand-name {
    display: block;
  }

  .status {
    justify-content: flex-start;
    padding: 10px 16px;
  }

  .status-label {
    display: block;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sidebar {
    transition: none;
  }
}
</style>
