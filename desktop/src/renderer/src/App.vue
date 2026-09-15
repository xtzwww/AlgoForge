<script setup lang="ts">
/**
 * App shell — sidebar + top strip + content area (PU-6).
 *
 * Previously this was a horizontal tab bar over the content. PU-6 turns
 * it into the sidebar shell the visual rewrite (PU-7) builds on, and
 * makes it survive a browser window that the user can resize at will.
 *
 * What PU-6 does *not* do, because PU-7 owns it: introduce vue-router,
 * add breadcrumbs, or wire the sidebar footer to `env-check`. Navigation
 * is still a `ref<Tab>` here.
 *
 * PU-14 note: the catalogue's "which problem am I practising" is what a
 * router would carry as a `problemId` route param. Without one, this
 * shell owns it as `selectedProblemId` and hands it to Practice as a
 * prop, so picking a problem switches pages *and* loads the problem in
 * one interaction.
 *
 * Responsive behaviour lives in the two components (AppSidebar's media
 * queries, TitleBar's toggle). This file owns only the one piece of
 * state that cannot be expressed in CSS: whether the drawer is open.
 */
import { onMounted, onUnmounted, ref } from 'vue'
import Practice from './views/Practice.vue'
import MistakesView from './views/MistakesView.vue'
import ProfileView from './views/ProfileView.vue'
import PlanView from './views/PlanView.vue'
import ProblemListView from './views/ProblemListView.vue'
import AppSidebar, { type NavItem } from './components/AppSidebar.vue'
import TitleBar from './components/TitleBar.vue'
import { initHttpClient } from './api/http'

type Tab = 'practice' | 'problems' | 'mistakes' | 'profile' | 'plan'
const activeTab = ref<Tab>('practice')

/**
 * The nav list. Icons are 24×24 path data drawn by AppSidebar; see its
 * docstring for why they are inline rather than an icon dependency.
 */
const NAV_ITEMS: NavItem[] = [
  { id: 'practice', label: '练习', path: 'M8 18l-5-6 5-6M16 6l5 6-5 6' },
  {
    id: 'problems',
    label: '题库',
    path: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01'
  },
  {
    id: 'mistakes',
    label: '错题本',
    path: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'
  },
  {
    id: 'profile',
    label: '画像',
    path: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'
  },
  {
    id: 'plan',
    label: '复习',
    path: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z'
  }
]

/**
 * Whether the off-canvas drawer is open. Only meaningful below 1024px —
 * above that the sidebar is always present and CSS ignores this flag, so
 * a window grown past the breakpoint cannot leave a stale drawer.
 */
const drawerOpen = ref(false)

/**
 * The problem Practice is pointed at, or null for free-run mode.
 *
 * Owned here rather than in Practice so that returning to the practice
 * page by clicking its nav item shows the same problem the user was on
 * (the page is a `v-if`, so Practice is unmounted and remounted on every
 * switch — local state would not survive).
 */
const selectedProblemId = ref<number | null>(null)

/** Switch pages, and close the drawer if one is open. */
function navigate(id: string): void {
  activeTab.value = id as Tab
  drawerOpen.value = false
}

/**
 * Practise a problem: remember it and move to the practice page.
 *
 * The catalogue's `select` event carries the id; App is what knows both
 * halves of "go to the editor for this problem".
 */
function openProblem(problemId: number): void {
  selectedProblemId.value = problemId
  activeTab.value = 'practice'
}

/** Clear the picked problem, dropping Practice back to free-run mode. */
function exitToFreeRun(): void {
  selectedProblemId.value = null
}

/**
 * Escape closes the drawer.
 *
 * Registered on the window rather than on the drawer element because
 * focus is usually still in the content when the user decides to back
 * out; requiring them to click into the drawer first would make the key
 * unreliable.
 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && drawerOpen.value) {
    drawerOpen.value = false
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  try {
    await initHttpClient()
    console.log('[app] http client initialized')
  } catch (err) {
    console.error('[app] failed to init http client:', err)
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="app-shell">
    <TitleBar :drawer-open="drawerOpen" @toggle-drawer="drawerOpen = !drawerOpen" />

    <div class="app-body">
      <AppSidebar
        :items="NAV_ITEMS"
        :active="activeTab"
        :drawer-open="drawerOpen"
        @navigate="navigate"
      />

      <!--
        Scrim behind the drawer. Present in the DOM at every width but
        only painted below 1024px, so it cannot swallow clicks on a wide
        screen where the sidebar is docked rather than floating.
      -->
      <div
        class="drawer-scrim"
        :class="{ visible: drawerOpen }"
        aria-hidden="true"
        @click="drawerOpen = false"
      />

      <main class="app-content">
        <ProblemListView v-if="activeTab === 'problems'" @select="openProblem" />
        <Practice
          v-else-if="activeTab === 'practice'"
          :problem-id="selectedProblemId"
          @exit="exitToFreeRun"
        />
        <MistakesView v-else-if="activeTab === 'mistakes'" />
        <ProfileView v-else-if="activeTab === 'profile'" />
        <PlanView v-else-if="activeTab === 'plan'" />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  /* PU-6: the shell owns the viewport now, so it must not scroll itself
   * — each page keeps its own scroll container. */
  overflow: hidden;
  background: var(--af-canvas);
}

.app-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

/* A flex column so a page can fill it with `height: 100%`. Before PU-6
 * the pages were direct children of a full-height wrapper and sized
 * themselves with `100vh`, which no longer works now that the title bar
 * occupies part of the viewport in the web shell. */
.app-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.drawer-scrim {
  display: none;
}

@media (max-width: 1023px) {
  .drawer-scrim {
    display: block;
    position: fixed;
    /* Below the title bar, matching the drawer (see AppSidebar). Covering
     * the title bar would put the scrim over the hamburger and make the
     * drawer impossible to close with the control that opened it. */
    top: var(--spacing-topbar);
    right: 0;
    bottom: 0;
    left: 0;
    z-index: 30;
    background: rgb(0 0 0 / 0.5);
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--af-duration-base) var(--af-ease);
  }

  .drawer-scrim.visible {
    opacity: 1;
    pointer-events: auto;
  }
}

@media (prefers-reduced-motion: reduce) {
  .drawer-scrim {
    transition: none;
  }
}
</style>
