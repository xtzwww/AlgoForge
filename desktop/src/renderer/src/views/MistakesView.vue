<script setup lang="ts">
/**
 * MistakesView — the mistake-book page (P1-21).
 *
 * Layout:
 *   ┌── Tag filter bar ────────────────────────────────────┐
 *   │  [全部] [dp] [贪心] [二分] ...                         │
 *   ├────────────────────────────────────────────────────────┤
 *   │  Entry card:                                          │
 *   │    Problem #id · created_at · reviewed badge           │
 *   │    error_cause text                                    │
 *   │    knowledge_tags chips                                │
 *   │    [标记已复习] [删除]                                  │
 *   ├────────────────────────────────────────────────────────┤
 *   │  ... more entries                                      │
 *   └────────────────────────────────────────────────────────┘
 *
 * Tag filtering is client-side: we load all entries once, then
 * filter the displayed list by the selected tag. The tag bar is
 * built from the union of knowledge_tags across all loaded entries.
 */
import { computed, onMounted, ref } from 'vue'
import {
  deleteMistake,
  listMistakes,
  updateMistake,
  type MistakeEntry,
} from '../api/mistake_book'

const entries = ref<MistakeEntry[]>([])
const loading = ref(false)
const errorMsg = ref('')
const selectedTag = ref<string | null>(null) // null = 全部

/** All unique tags across all loaded entries, sorted. */
const allTags = computed<string[]>(() => {
  const set = new Set<string>()
  for (const e of entries.value) {
    for (const t of e.knowledge_tags) set.add(t)
  }
  return Array.from(set).sort()
})

/** Entries filtered by the selected tag (null = show all). */
const filteredEntries = computed<MistakeEntry[]>(() => {
  if (selectedTag.value === null) return entries.value
  return entries.value.filter((e) => e.knowledge_tags.includes(selectedTag.value!))
})

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    entries.value = await listMistakes()
  } catch (err) {
    errorMsg.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

async function markReviewed(entry: MistakeEntry): Promise<void> {
  try {
    const updated = await updateMistake(entry.id, { reviewed: true })
    const idx = entries.value.findIndex((e) => e.id === entry.id)
    if (idx !== -1) entries.value[idx] = updated
  } catch (err) {
    errorMsg.value = (err as Error).message
  }
}

async function remove(entry: MistakeEntry): Promise<void> {
  try {
    await deleteMistake(entry.id)
    entries.value = entries.value.filter((e) => e.id !== entry.id)
  } catch (err) {
    errorMsg.value = (err as Error).message
  }
}

function selectTag(tag: string | null): void {
  selectedTag.value = tag
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

onMounted(load)
</script>

<template>
  <div class="mistakes-view">
    <header class="header">
      <h1>错题本</h1>
      <button class="refresh-btn" :disabled="loading" @click="load">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
    </header>

    <!-- Tag filter bar -->
    <nav class="tag-bar">
      <button
        class="tag-chip"
        :class="{ active: selectedTag === null }"
        @click="selectTag(null)"
      >
        全部 ({{ entries.length }})
      </button>
      <button
        v-for="tag in allTags"
        :key="tag"
        class="tag-chip"
        :class="{ active: selectedTag === tag }"
        @click="selectTag(tag)"
      >
        {{ tag }}
      </button>
    </nav>

    <!-- Error message -->
    <div v-if="errorMsg" class="error-msg">
      请求失败: {{ errorMsg }}
    </div>

    <!-- Empty state -->
    <div v-if="!loading && filteredEntries.length === 0 && !errorMsg" class="empty">
      <template v-if="entries.length === 0">
        错题本为空。判题失败后，可在练习页面将错题加入错题本。
      </template>
      <template v-else>
        所选知识点下暂无错题。
      </template>
    </div>

    <!-- Entry list -->
    <div v-if="filteredEntries.length > 0" class="entry-list">
      <article
        v-for="entry in filteredEntries"
        :key="entry.id"
        class="entry-card"
        :class="{ reviewed: entry.reviewed }"
      >
        <div class="entry-header">
          <span class="entry-title">题目 #{{ entry.problem_id }}</span>
          <span class="entry-date">{{ formatDate(entry.created_at) }}</span>
          <span v-if="entry.reviewed" class="badge badge-reviewed">已复习</span>
          <span v-else class="badge badge-unreviewed">未复习</span>
        </div>

        <p v-if="entry.error_cause" class="entry-cause">
          {{ entry.error_cause }}
        </p>
        <p v-else class="entry-cause placeholder">
          （尚未填写错因）
        </p>

        <div class="entry-tags">
          <span v-for="tag in entry.knowledge_tags" :key="tag" class="entry-tag">
            {{ tag }}
          </span>
        </div>

        <div class="entry-meta">
          <span class="meta-item">提交 #{{ entry.submission_id }}</span>
          <span class="meta-item">复习 {{ entry.review_count }} 次</span>
          <span v-if="entry.last_reviewed_at" class="meta-item">
            上次复习: {{ formatDate(entry.last_reviewed_at) }}
          </span>
        </div>

        <div class="entry-actions">
          <button
            v-if="!entry.reviewed"
            class="action-btn btn-review"
            @click="markReviewed(entry)"
          >
            标记已复习
          </button>
          <button class="action-btn btn-delete" @click="remove(entry)">
            删除
          </button>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.mistakes-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding: 16px;
  box-sizing: border-box;
  color: #e5e7eb;
  background: #1a1a2e;
  overflow-y: auto;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.header h1 {
  margin: 0;
  font-size: 22px;
}

.refresh-btn {
  padding: 6px 16px;
  font-size: 13px;
  color: white;
  background: #3b82f6;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.refresh-btn:disabled {
  background: #6b7280;
  cursor: not-allowed;
}

.tag-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #374151;
}

.tag-chip {
  padding: 4px 12px;
  font-size: 13px;
  color: #d1d5db;
  background: #374151;
  border: none;
  border-radius: 16px;
  cursor: pointer;
  transition: background 0.2s;
}

.tag-chip:hover {
  background: #4b5563;
}

.tag-chip.active {
  background: #3b82f6;
  color: white;
}

.error-msg {
  color: #ef4444;
  padding: 8px 12px;
  background: #7f1d1d;
  border-radius: 4px;
  margin-bottom: 12px;
}

.empty {
  color: #6b7280;
  text-align: center;
  padding: 40px 16px;
  font-size: 14px;
}

.entry-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.entry-card {
  padding: 16px;
  background: #16213e;
  border-radius: 8px;
  border-left: 4px solid #ef4444;
}

.entry-card.reviewed {
  border-left-color: #22c55e;
  opacity: 0.7;
}

.entry-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.entry-title {
  font-weight: bold;
  font-size: 15px;
}

.entry-date {
  color: #9ca3af;
  font-size: 12px;
}

.badge {
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 10px;
}

.badge-reviewed {
  background: #14532d;
  color: #86efac;
}

.badge-unreviewed {
  background: #7c2d12;
  color: #fdba74;
}

.entry-cause {
  margin: 8px 0;
  font-size: 14px;
  line-height: 1.5;
  color: #e5e7eb;
}

.entry-cause.placeholder {
  color: #6b7280;
  font-style: italic;
}

.entry-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0;
}

.entry-tag {
  padding: 2px 8px;
  font-size: 12px;
  color: #93c5fd;
  background: #1e3a5f;
  border-radius: 4px;
}

.entry-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 8px;
}

.entry-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.action-btn {
  padding: 4px 12px;
  font-size: 12px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.btn-review {
  color: white;
  background: #22c55e;
}

.btn-review:hover {
  background: #16a34a;
}

.btn-delete {
  color: white;
  background: #ef4444;
}

.btn-delete:hover {
  background: #dc2626;
}
</style>
