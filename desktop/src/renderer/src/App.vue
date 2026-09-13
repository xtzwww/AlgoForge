<script setup lang="ts">
/**
 * App shell — holds a simple tab switcher between the four main
 * pages. P1-21 added mistakes, P1-22 added profile, P1-23 adds plan.
 *
 * vue-router is intentionally avoided (single-user desktop app, few
 * pages) — a `ref<Tab>` is enough.
 */
import { onMounted, ref } from 'vue'
import Practice from './views/Practice.vue'
import MistakesView from './views/MistakesView.vue'
import ProfileView from './views/ProfileView.vue'
import PlanView from './views/PlanView.vue'
import { initHttpClient } from './api/http'

type Tab = 'practice' | 'mistakes' | 'profile' | 'plan'
const activeTab = ref<Tab>('practice')

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
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'plan' }"
        @click="activeTab = 'plan'"
      >
        复习
      </button>
    </nav>
    <main class="tab-content">
      <Practice v-if="activeTab === 'practice'" />
      <MistakesView v-else-if="activeTab === 'mistakes'" />
      <ProfileView v-else-if="activeTab === 'profile'" />
      <PlanView v-else-if="activeTab === 'plan'" />
    </main>
  </div>
</template>

<style>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.tab-bar {
  display: flex;
  background: #0f172a;
  padding: 0 16px;
  border-bottom: 1px solid #1e293b;
  flex-shrink: 0;
}

.tab-btn {
  padding: 12px 24px;
  font-size: 14px;
  color: #9ca3af;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s;
}

.tab-btn:hover {
  color: #e5e7eb;
}

.tab-btn.active {
  color: #e5e7eb;
  border-bottom-color: #3b82f6;
}

.tab-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
