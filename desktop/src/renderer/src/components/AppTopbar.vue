<script setup lang="ts">
/**
 * AppTopbar — breadcrumbs, brand and environment badge (PU-7).
 *
 * Replaces PU-6's `TitleBar`, which carried only the brand and the
 * drawer toggle. The topbar now renders in **both** shells rather than
 * hiding itself in Electron: breadcrumbs are app navigation, not chrome,
 * and a desktop window has no other place to show them. Electron ends up
 * with the OS title bar above this one, which is what every editor-style
 * desktop app looks like.
 *
 * What it owns:
 *
 * - **Breadcrumbs**, built from the route. A route's own crumb is its
 *   `meta.title`, unless the page published a better label — see
 *   `composables/usePageCrumb.ts` for why `/problems/:id` needs that.
 *   The rule is deliberately uniform rather than a special case for the
 *   problem route, so a future detail page gets the same behaviour for
 *   free.
 * - **The drawer toggle.** Only visible below 1024px, but it lives here
 *   rather than in the sidebar because an Electron window can be
 *   narrowed past that breakpoint too, and a control that vanishes along
 *   with the thing it opens is worse than useless.
 * - **The environment badge**, which is where `RuntimeCapabilities`
 *   reaches the UI: it reports what this shell cannot do and why, rather
 *   than shipping a dead control. See PU-6's note — the shape to follow
 *   when a real "export to file" button arrives.
 *
 * **Styling.** This component is written in Tailwind utilities, unlike
 * `AppSidebar.vue`: every rule here is static layout that maps to a
 * single utility, which is the case utilities are good at. The sidebar's
 * rules are compound (breakpoint × state), which is the case they are
 * not — see its docstring.
 *
 * The `[var(--spacing-*)]` form is used instead of Tailwind's named
 * spacing scale so the PU-0 tokens stay the single source of truth: a
 * utility like `h-12` would silently duplicate `--spacing-topbar` and
 * drift from it.
 */

import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter, type RouteLocationRaw } from 'vue-router'
import { runtime } from '../runtime'
import { usePageCrumb } from '../composables/usePageCrumb'
import { isLoggedIn, isTeacher, logout, sessionReady, sessionUser } from '../api/session'
import ThemeSwitcher from './ThemeSwitcher.vue'

const props = defineProps<{
  /** Drawer state, so the toggle can report it to assistive tech. */
  drawerOpen?: boolean
}>()

const emit = defineEmits<{ (e: 'toggle-drawer'): void }>()

const route = useRoute()
const router = useRouter()
const publishedCrumb = usePageCrumb()

/** One rendered crumb. The last one has no target — it is where you are. */
interface Crumb {
  label: string
  to?: RouteLocationRaw
}

const crumbs = computed<Crumb[]>(() => {
  const list: Crumb[] = []

  // `meta.parent` names the route one level up, so the chain is walked
  // rather than the path split. Splitting would produce a crumb for every
  // path segment, including ones that are not pages.
  const parentName = route.meta.parent
  if (typeof parentName === 'string') {
    const parent = router.resolve({ name: parentName })
    list.push({ label: String(parent.meta.title ?? parentName), to: { name: parentName } })
  }

  list.push({ label: publishedCrumb.value ?? String(route.meta.title ?? '') })
  return list
})

/**
 * Only the browser shell needs a brand here; Electron's window already
 * shows the title.
 */
const isWeb = computed<boolean>(() => runtime.kind === 'web')

/**
 * What this shell cannot do, phrased for a user rather than a developer.
 * Built from the capability flags so adding one to the runtime forces a
 * decision here about how to describe it.
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
const badgeTitle = computed<string>(() =>
  limitations.value.length === 0
    ? `${runtime.displayName}：功能完整`
    : `${runtime.displayName}，以下功能不可用：\n· ${limitations.value.join('\n· ')}`
)

/**
 * The account corner (PU-13).
 *
 * `sessionReady` gates the signed-out state rather than `!signedIn`
 * alone: before the startup round trip resolves the app does not know,
 * and flashing 「登录」 at somebody who is already signed in — then
 * replacing it a moment later — reads as the session having dropped.
 */
const signedIn = computed<boolean>(() => isLoggedIn.value)
const who = computed<string>(
  () => sessionUser.value?.display_name || sessionUser.value?.username || ''
)
const roleLabel = computed<string>(() => (isTeacher.value ? '教师' : '学生'))

const signingOut = ref(false)

/**
 * Sign out, then go to the login page.
 *
 * The redirect is explicit rather than left to a route guard: this
 * topbar is the only place that signs anyone out, and a guard watching
 * every route would be a much larger change than the one thing it needs
 * to do here.
 */
async function signOut(): Promise<void> {
  if (signingOut.value) return
  signingOut.value = true
  try {
    await logout()
    await router.replace({ name: 'login' })
  } finally {
    signingOut.value = false
  }
}
</script>

<template>
  <!--
    `relative` exists so the beam below can be pinned to the bottom edge;
    the translucent surface plus blur is the glass treatment the rest of
    the repaint uses, written here as utilities rather than as the
    `af-glass` class because that class sets a 1px border on all four
    sides and this bar only has a bottom one (see motion.css).

    The beam overlays the bottom border rather than replacing it: a
    separator that fades to transparent at both ends stops separating.
  -->
  <header
    class="relative flex h-[var(--spacing-topbar)] shrink-0 items-center gap-3 border-b border-af-border bg-af-surface/75 px-3 backdrop-blur-xl backdrop-saturate-150"
  >
    <span class="af-beam pointer-events-none absolute inset-x-0 -bottom-px h-px" aria-hidden="true" />

    <!-- The visual-style control. First in the bar so it sits in the
         top-left corner on every page; it owns no layout of its own. -->
    <ThemeSwitcher />

    <button
      class="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--af-radius-sm)] text-af-secondary transition-colors hover:bg-af-elevated hover:text-af-primary lg:hidden"
      type="button"
      :aria-expanded="props.drawerOpen ? 'true' : 'false'"
      aria-label="切换导航菜单"
      @click="emit('toggle-drawer')"
    >
      <svg
        class="h-5 w-5"
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
        class="h-[22px] w-[22px] shrink-0 text-af-accent"
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
      <span class="shrink-0 text-[15px] font-semibold tracking-[0.01em] text-af-primary">
        AlgoForge
      </span>
    </template>

    <nav class="flex min-w-0 items-center gap-1.5 text-xs" aria-label="面包屑">
      <template v-for="(crumb, index) in crumbs" :key="`${index}-${crumb.label}`">
        <span v-if="index > 0" class="shrink-0 text-af-muted" aria-hidden="true">/</span>
        <RouterLink
          v-if="crumb.to"
          class="truncate text-af-secondary transition-colors hover:text-af-primary"
          :to="crumb.to"
        >
          {{ crumb.label }}
        </RouterLink>
        <span v-else class="truncate font-medium text-af-primary" aria-current="page">
          {{ crumb.label }}
        </span>
      </template>
    </nav>

    <div class="ml-auto flex shrink-0 items-center gap-2">
      <template v-if="signedIn">
        <span class="shrink-0 text-xs text-af-secondary">
          {{ who }}
          <!--
            The role chip is tinted, not just labelled (2026-09-25). The
            two roles see genuinely different navigation, so a teacher
            glancing at a shared screen should be able to tell which
            account is signed in without reading four characters.
          -->
          <span
            class="ml-1 rounded-full border px-1.5 py-0.5 text-[11px]"
            :class="
              isTeacher
                ? 'border-af-accent/40 bg-af-accent/10 text-af-accent-hover'
                : 'border-af-border bg-af-elevated text-af-muted'
            "
          >
            {{ roleLabel }}
          </span>
        </span>
        <button
          class="af-press shrink-0 rounded-[var(--af-radius-sm)] border border-af-border-strong bg-af-elevated px-2.5 py-0.5 text-[11px] text-af-secondary transition-colors hover:bg-af-overlay hover:text-af-primary disabled:opacity-60"
          type="button"
          :disabled="signingOut"
          @click="signOut"
        >
          {{ signingOut ? '登出中…' : '登出' }}
        </button>
      </template>
      <RouterLink
        v-else-if="sessionReady"
        class="af-press shrink-0 rounded-[var(--af-radius-sm)] border border-af-border-strong bg-af-elevated px-2.5 py-0.5 text-[11px] text-af-secondary transition-colors hover:bg-af-overlay hover:text-af-primary"
        :to="{ name: 'login' }"
      >
        登录
      </RouterLink>

      <span
        class="shrink-0 cursor-help rounded-full border border-af-border bg-af-elevated px-2.5 py-0.5 text-[11px] text-af-secondary"
        :title="badgeTitle"
      >
        {{ runtime.displayName }}
      </span>
    </div>
  </header>
</template>
