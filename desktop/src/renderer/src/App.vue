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
 * 0. **Which shell.** 2026-10-04 split the app into two structures: the
 *    regular sidebar chrome, and a full-screen card for the sign-in /
 *    sign-up pages (`meta.layout === 'auth'`). The auth pages are a pure
 *    form — no sidebar to wander off to, no engine-status footer, and,
 *    deliberately, **no startup gate**: being told "the engine is not
 *    connected" while trying to sign in is circular, because signing in
 *    is a precondition for the engine being any use. Everything below
 *    about the gate applies to the `app` branch only.
 * 1. **The drawer's open/closed state.** The only piece of viewport state
 *    CSS cannot express. It is ignored above 1024px, so a window grown
 *    past the breakpoint cannot leave a stale open drawer behind.
 * 2. **The startup gate.** Until the sidecar has answered, there is
 *    nothing to show a page: `initHttpClient()` has not run, so every
 *    request a page made would fail for a reason the page cannot fix.
 *    The content area shows a skeleton, or the failure and a retry.
 * 3. **The page transition.** The one animation that has to live above
 *    every page, because no individual page can animate itself in and
 *    out of existence. The classes it uses are in `assets/motion.css`.
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

import { onMounted, onUnmounted, ref, computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import AppSidebar from './components/AppSidebar.vue'
import AppTopbar from './components/AppTopbar.vue'
import { useSidecarStatus } from './composables/useSidecarStatus'
import { useNaiveTheme } from './components/naive-theme'
import { usesAuthLayout } from './router/meta'

/**
 * 主题三件套（2026-09-26）：把 `--af-*` 那三套调色板翻成 Naive UI 能吃的
 * 配置，喂给 `NConfigProvider`。**必须挂在根上**——Naive 的主题是
 * `provide` 下去的，包在页面上不生效。
 *
 * 这是这次引入组件库唯一必须改壳的地方，其余都是各页面自己换控件。
 */
const { theme: naiveTheme, themeOverrides } = useNaiveTheme()

/**
 * Whether the off-canvas drawer is open. Only meaningful below 1024px —
 * above that the sidebar is always present and CSS ignores this flag.
 */
const drawerOpen = ref(false)

const { phase, errorMessage, retry } = useSidecarStatus()

const route = useRoute()

/**
 * 认证页走全屏外壳（2026-10-04）。
 *
 * 登录/注册页原先和其余页面一样渲染在侧边栏壳里，于是登录屏上能看到
 * 品牌行、导航、引擎状态条，还能直接点侧边栏跳去题库/练习（它们是公开
 * 的）。用法是"登录前就该是一张干净的卡片"，所以这两页不渲染外壳。
 *
 * 响应式：`route.meta` 是 `RouteLocationNormalized` 上的普通对象，路由
 * 变化时整个 `route` 会被替换，`computed` 因此能跟着切。
 */
const authLayout = computed<boolean>(() => usesAuthLayout(route.meta))

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
  <!--
    `NConfigProvider` 是 Naive UI 的主题入口：`theme` 决定底座（深色用
    `darkTheme`，浅色给 null），`themeOverrides` 才是本项目的三套配色。
    没有它，Naive 的组件会用自己那套蓝白配色，和页面其余部分（`--af-*`）
    在同一个屏幕上并存两套颜色——正是这次升级要消掉的问题。

    `NMessageProvider` / `NDialogProvider` 是**命令式** API 的宿主
    （`useMessage()` 弹提示、`useDialog()` 弹确认框）。它们必须包在
    任何调用 `useMessage()` 的组件之上，所以放在这里；单个页面自己包
    一份的话，提示会跟着该页面一起被卸载。

    No background on the root (2026-09-25). This element used to carry
    `bg-af-canvas`, an opaque fill across the entire viewport — which
    would have covered the canvas glow and grid painted on `body` and
    made the single most distinctive part of the repaint invisible. The
    canvas belongs to `body` (see tokens.css); the shell's job is to not
    paint over it.
  -->
  <NConfigProvider :theme="naiveTheme" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <NDialogProvider>
        <!--
          全屏认证页（2026-10-04）。`layout: 'auth'` 的路由（登录/注册）
          走这一支：没有侧边栏、没有顶栏、没有引擎状态条，整屏就是居中
          的卡片。这一层只负责撑满视口（`min-h-screen` + flex 列），背景
          沿用 `body` 上的画布——App 壳"不覆盖画布"的那条约定在这里同样
          成立，所以这里也不画底色。
        -->
        <div v-if="authLayout" class="flex min-h-screen flex-col">
          <RouterView v-slot="{ Component }">
            <Transition name="af-page" mode="out-in">
              <component :is="Component" />
            </Transition>
          </RouterView>
        </div>

        <div v-else class="flex h-screen flex-col overflow-hidden">
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
              :class="
                drawerOpen ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0'
              "
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
                <div
                  class="h-6 w-1/3 animate-pulse rounded bg-af-elevated motion-reduce:animate-none"
                />
                <div
                  class="h-4 w-2/3 animate-pulse rounded bg-af-elevated motion-reduce:animate-none"
                />
                <div
                  class="h-4 w-1/2 animate-pulse rounded bg-af-elevated motion-reduce:animate-none"
                />
                <div
                  class="mt-2 h-40 w-full animate-pulse rounded bg-af-surface motion-reduce:animate-none"
                />
              </div>

              <!--
          The routed page, wrapped in a transition (2026-09-25).

          `mode="out-in"` rather than the default simultaneous mode: two
          pages cross-fading in the same box both need `position: absolute`
          to overlap, and taking a flex child out of flow for the duration
          of a navigation is how a shell acquires a one-frame layout jump.
          Out-in sidesteps that entirely at the cost of ~120ms, which is
          the leaving half being shortened on purpose (see motion.css).

          No `:key` on purpose. Keying by `route.path` would force a
          remount on `/problems/12` → `/problems/13`, and the router
          deliberately keeps that instance alive so the statement panel
          holds its scroll position (see the note in `router/index.ts`).
          Different routes resolve to different components, which is
          enough for `<Transition>` to notice — the key is only required
          when the *same* component serves both routes.
        -->
              <RouterView v-else v-slot="{ Component }">
                <Transition name="af-page" mode="out-in">
                  <component :is="Component" />
                </Transition>
              </RouterView>
            </main>
          </div>
        </div>
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
