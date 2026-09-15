<script setup lang="ts">
/**
 * App shell — sidebar + topbar + routed content area (PU-6, rerouted in
 * PU-7).
 *
 * PU-6 turned a flat tab bar into a sidebar shell that survives a
 * resizable window. PU-7 replaces the `ref<Tab>` + `v-if` chain with
 * vue-router, so this file no longer knows what the five pages are: it
 * renders `<RouterView>` and the navigation lives in the route table
 * (`router/index.ts`).
 *
 * What is left here is genuinely shell business:
 *
 * 1. **The drawer's open/closed state.** The only piece of viewport state
 *    CSS cannot express. It is ignored above 1024px, so a window grown
 *    past the breakpoint cannot leave a stale open drawer behind.
 * 2. **The startup gate.** Until the sidecar has answered, there is
 *    nothing to show a page: `initHttpClient()` has not run, so every
 *    request a page made would fail for a reason the page cannot fix.
 *    The content area shows a skeleton, or the failure and a retry.
 *
 * The gate is why this file reads the shared status from
 * `composables/useSidecarStatus.ts` rather than probing itself — the
 * sidebar footer reports the same answer, and two probes could disagree.
 *
 * **Styling.** Tailwind utilities, following the split described in
 * `AppSidebar.vue`: the shell's layout is static and the drawer's state
 * is a pair of mutually exclusive class lists, so nothing here needs the
 * compound selectors that pushed the sidebar's rules into scoped CSS.
 */

import { onMounted, onUnmounted, ref } from 'vue'
import { RouterView } from 'vue-router'
import AppSidebar from './components/AppSidebar.vue'
import AppTopbar from './components/AppTopbar.vue'
import { useSidecarStatus } from './composables/useSidecarStatus'

/**
 * Whether the off-canvas drawer is open. Only meaningful below 1024px —
 * above that the sidebar is always present and CSS ignores this flag.
 */
const drawerOpen = ref(false)

const { phase, errorMessage, retry } = useSidecarStatus()

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

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="flex h-screen flex-col overflow-hidden bg-af-canvas">
    <AppTopbar :drawer-open="drawerOpen" @toggle-drawer="drawerOpen = !drawerOpen" />

    <div class="flex min-h-0 flex-1">
      <AppSidebar :drawer-open="drawerOpen" @navigate="drawerOpen = false" />

      <!--
        Scrim behind the drawer. Present in the DOM at every width but
        only painted below 1024px, so it cannot swallow clicks on a wide
        screen where the sidebar is docked rather than floating. The two
        states are bound as whole class lists so the class attribute never
        holds two conflicting utilities at once — which utility would win
        is decided by stylesheet order, not by class order.
      -->
      <div
        class="fixed inset-x-0 bottom-0 top-[var(--spacing-topbar)] z-30 bg-black/50 transition-opacity duration-200 lg:hidden"
        :class="drawerOpen ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0'"
        aria-hidden="true"
        @click="drawerOpen = false"
      />

      <main class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <!--
          The sidecar never answered. Retrying is worth offering because
          the likely causes are all transient or fixable: the engine is
          still starting, or it is not running at all.
        -->
        <div v-if="phase === 'failed'" class="flex flex-1 items-center justify-center p-6">
          <div class="max-w-md text-center">
            <p class="text-sm font-medium text-af-primary">无法连接到分析引擎</p>
            <p class="mt-2 text-xs leading-relaxed text-af-secondary">{{ errorMessage }}</p>
            <button
              class="mt-4 rounded-[var(--af-radius-sm)] border border-af-border-strong bg-af-elevated px-4 py-1.5 text-xs text-af-primary transition-colors hover:bg-af-overlay"
              type="button"
              @click="retry"
            >
              重试
            </button>
          </div>
        </div>

        <!--
          The engine has not answered yet. A skeleton rather than a blank
          area, because the shell is already up and the wait is real: the
          first env-check launches a version probe per language.
        -->
        <div
          v-else-if="phase === 'checking'"
          class="flex flex-1 flex-col gap-3 p-6"
          aria-busy="true"
          aria-live="polite"
        >
          <span class="sr-only">正在连接分析引擎…</span>
          <div class="h-6 w-1/3 animate-pulse rounded bg-af-elevated motion-reduce:animate-none" />
          <div class="h-4 w-2/3 animate-pulse rounded bg-af-elevated motion-reduce:animate-none" />
          <div class="h-4 w-1/2 animate-pulse rounded bg-af-elevated motion-reduce:animate-none" />
          <div
            class="mt-2 h-40 w-full animate-pulse rounded bg-af-surface motion-reduce:animate-none"
          />
        </div>

        <RouterView v-else />
      </main>
    </div>
  </div>
</template>
