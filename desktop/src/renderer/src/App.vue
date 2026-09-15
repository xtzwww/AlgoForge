<script setup lang="ts">
/**
 * App shell — holds a simple tab switcher between the five main
 * pages. P1-21 added mistakes, P1-22 added profile, P1-23 adds plan,
 * PU-14 adds the problem catalogue.
 *
 * vue-router is intentionally avoided (single-user desktop app, few
 * pages) — a `ref<Tab>` is enough.
 *
 * PU-14 note: the catalogue's "which problem am I practising" is what
 * a router would carry as a `problemId` route param. Without one, this
 * shell owns it as `selectedProblemId` and hands it to Practice as a
 * prop, so picking a problem switches tabs *and* loads the problem in
 * one interaction.
 */
import { onMounted, ref } from 'vue'
import Practice from './views/Practice.vue'
import MistakesView from './views/MistakesView.vue'
import ProfileView from './views/ProfileView.vue'
import PlanView from './views/PlanView.vue'
import ProblemListView from './views/ProblemListView.vue'
import { initHttpClient } from './api/http'

type Tab = 'practice' | 'problems' | 'mistakes' | 'profile' | 'plan'
const activeTab = ref<Tab>('practice')

/**
 * The problem Practice is pointed at, or null for free-run mode.
 *
 * Owned here rather than in Practice so that returning to the practice
 * tab by clicking its button shows the same problem the user was on
 * (the tab is a `v-if`, so Practice is unmounted and remounted on every
 * switch — local state would not survive).
 */
const selectedProblemId = ref<number | null>(null)

/**
 * Practise a problem: remember it and move to the practice tab.
 *
 * The catalogue's `select` event carries the id; App is what knows
 * both halves of "go to the editor for this problem".
 */
function openProblem(problemId: number): void {
  selectedProblemId.value = problemId
  activeTab.value = 'practice'
}

/** Clear the picked problem, dropping Practice back to free-run mode. */
function exitToFreeRun(): void {
  selectedProblemId.value = null
}

onMounted(async () => {
  try {
    await initHttpClient()
    console.log('[app] http client initialized')
  } catch (err) {
    console.error('[app] failed to init http client:', err)
  }
})
</script>

<template>
  <div class="app">
    <nav class="tab-bar">
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'practice' }"
        @click="activeTab = 'practice'"
      >
        练习
      </button>
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'problems' }"
        @click="activeTab = 'problems'"
      >
        题库
      </button>
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'mistakes' }"
        @click="activeTab = 'mistakes'"
      >
        错题本
      </button>
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'profile' }"
        @click="activeTab = 'profile'"
      >
        画像
      </button>
      <button class="tab-btn" :class="{ active: activeTab === 'plan' }" @click="activeTab = 'plan'">
        复习
      </button>
      <!-- PU-0 probe: Tailwind utilities (bg-af-*, text-af-*) must render here.
           If this badge has no background/rounding, Tailwind is not wired up. -->
      <span class="token-probe bg-af-accent-subtle text-af-accent rounded-full px-3 py-1">
        Tailwind v4 ✓
      </span>
    </nav>
    <main class="tab-content">
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
</template>

<style>
/* PU-0 note: these styles are intentionally still plain CSS — the visual
 * rewrite happens in PU-7 (app shell). What PU-0 proves is that Tailwind
 * utilities and the --af-* tokens are both live; see the `token-probe`
 * markup in the template. */
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.tab-bar {
  display: flex;
  background: var(--af-surface);
  padding: 0 16px;
  border-bottom: 1px solid var(--af-border);
  flex-shrink: 0;
  align-items: center;
}

.token-probe {
  margin-left: auto;
  font-size: 12px;
  color: var(--af-text-muted);
}

.tab-btn {
  padding: 12px 24px;
  font-size: 14px;
  color: var(--af-text-secondary);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition:
    color var(--af-duration-fast) var(--af-ease),
    border-color var(--af-duration-fast) var(--af-ease);
}

.tab-btn:hover {
  color: var(--af-text-primary);
}

.tab-btn.active {
  color: var(--af-text-primary);
  border-bottom-color: var(--af-accent);
}

.tab-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
