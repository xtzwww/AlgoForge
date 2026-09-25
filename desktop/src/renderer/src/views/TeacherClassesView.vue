<script setup lang="ts">
/**
 * TeacherClassesView — create classes, read out invite codes, manage
 * rosters (PU-13).
 *
 * Why the invite code is the loudest thing on the card
 * ----------------------------------------------------
 * It is the only part of this page a teacher reads *out loud* — to a
 * room, off a projector, once. Everything else here is something they
 * scan; this is the one value that has to survive being read aloud and
 * typed by thirty people, so it gets the largest type on the page and
 * letter spacing to keep the characters apart.
 *
 * Why a non-teacher sees an explanation rather than a 403 page
 * ------------------------------------------------------------
 * The page ships in the same bundle the students use, so a student can
 * reach this URL. The server refuses every call it makes — that is the
 * boundary (see `app/api/classroom.py`) — but an unexplained wall of
 * errors is a worse answer than saying who the page is for.
 */

import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  createClassroom,
  deleteClassroom,
  listClassMembers,
  listClassrooms,
  removeClassMember,
  type ClassMemberOut,
  type ClassroomOut
} from '../api/classroom'
import { isTeacher, sessionReady, sessionUser } from '../api/session'

const loading = ref(false)
const errorMsg = ref('')
const classrooms = ref<ClassroomOut[]>([])

const newName = ref('')
const creating = ref(false)
const createdHint = ref('')

/** Which class's roster is open, and its rows. */
const openId = ref<number | null>(null)
const members = ref<ClassMemberOut[]>([])
const membersLoading = ref(false)
const busyUserId = ref<number | null>(null)

const canManage = computed<boolean>(() => isTeacher.value)
const signedIn = computed<boolean>(() => sessionUser.value !== null)

function describe(err: unknown, fallback: string): string {
  const res = (err as { response?: { status?: number; data?: { detail?: string } } })?.response
  if (res?.status === 401) return '登录已过期，请重新登录'
  if (res?.status === 403) return '需要教师账号才能管理班级'
  if (res?.status === undefined) return '无法连接到分析引擎，请确认后端已启动'
  return res?.data?.detail ?? `${fallback}（HTTP ${res?.status}）`
}

async function load(): Promise<void> {
  if (!canManage.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    classrooms.value = await listClassrooms()
    // A class that disappeared (deleted here or elsewhere) must not
    // leave its roster open behind it.
    if (openId.value !== null && !classrooms.value.some((c) => c.id === openId.value)) {
      openId.value = null
      members.value = []
    }
  } catch (err) {
    errorMsg.value = describe(err, '无法加载班级')
  } finally {
    loading.value = false
  }
}

async function submitCreate(): Promise<void> {
  const name = newName.value.trim()
  if (!name || creating.value) return
  creating.value = true
  errorMsg.value = ''
  createdHint.value = ''
  try {
    const room = await createClassroom(name)
    newName.value = ''
    createdHint.value = `已创建「${room.name}」，邀请码 ${room.invite_code}`
    await load()
  } catch (err) {
    errorMsg.value = describe(err, '无法创建班级')
  } finally {
    creating.value = false
  }
}

async function toggleMembers(room: ClassroomOut): Promise<void> {
  if (openId.value === room.id) {
    openId.value = null
    members.value = []
    return
  }
  openId.value = room.id
  membersLoading.value = true
  members.value = []
  try {
    members.value = await listClassMembers(room.id)
  } catch (err) {
    errorMsg.value = describe(err, '无法加载班级成员')
  } finally {
    membersLoading.value = false
  }
}

async function dropMember(room: ClassroomOut, member: ClassMemberOut): Promise<void> {
  if (busyUserId.value !== null) return
  busyUserId.value = member.user_id
  errorMsg.value = ''
  try {
    await removeClassMember(room.id, member.user_id)
    await Promise.all([load(), listClassMembers(room.id).then((rows) => (members.value = rows))])
  } catch (err) {
    errorMsg.value = describe(err, `无法移除「${member.display_name}」`)
  } finally {
    busyUserId.value = null
  }
}

async function dropClass(room: ClassroomOut): Promise<void> {
  // Confirmed, unlike duplicating an assignment: this removes a class
  // the teacher may have spent time building, and its homework falls
  // back to "for everyone" — a change worth one extra click.
  const ok = window.confirm(
    `删除「${room.name}」？\n\n班级里的作业不会删除，会变成「所有人」可见。`
  )
  if (!ok) return
  errorMsg.value = ''
  try {
    await deleteClassroom(room.id)
    await load()
  } catch (err) {
    errorMsg.value = describe(err, '无法删除班级')
  }
}

// Waits for the startup round trip: before it resolves `isTeacher` is
// false for everybody, and loading on that would show "you are not a
// teacher" to the teacher.
watch(
  () => [sessionReady.value, isTeacher.value] as const,
  ([ready, teacher]) => {
    if (ready && teacher) void load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="flex-1 overflow-y-auto">
    <div class="mx-auto w-full max-w-[var(--af-content-max)] p-6">
      <h1 class="text-lg font-semibold text-af-primary">班级管理</h1>
      <p class="mt-1.5 text-xs leading-relaxed text-af-secondary">
        把邀请码发给学生，他们用学生账号加入后就能收到你布置的作业。
      </p>

      <!-- Not a teacher: say so instead of showing errors from every call. -->
      <div
        v-if="!canManage"
        class="af-glass mt-5 rounded-[var(--af-radius-lg)] p-6 text-center"
      >
        <p class="text-sm text-af-primary">
          {{ signedIn ? '这个页面只对教师账号开放' : '还没有登录' }}
        </p>
        <p class="mt-2 text-xs leading-relaxed text-af-secondary">
          {{
            signedIn
              ? '当前登录的账号是学生。班级由老师创建和管理。'
              : '请用老师分配的账号登录后再打开这个页面。'
          }}
        </p>
        <RouterLink
          v-if="!signedIn"
          class="mt-4 inline-block rounded-[var(--af-radius-sm)] bg-af-accent px-4 py-2 text-sm font-medium af-press text-af-inverse transition-colors hover:bg-af-accent-hover"
          :to="{ name: 'login' }"
        >
          去登录
        </RouterLink>
      </div>

      <template v-else>
        <!-- ---- Create ---- -->
        <section
          class="af-glass mt-5 rounded-[var(--af-radius-lg)] p-4"
        >
          <h2 class="text-sm font-medium text-af-primary">新建班级</h2>
          <form class="mt-3 flex flex-wrap items-center gap-2" @submit.prevent="submitCreate">
            <input
              v-model="newName"
              class="min-w-0 flex-1 rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated px-3 py-2 text-sm text-af-primary placeholder:text-af-muted focus:border-af-accent focus:outline-none disabled:opacity-60"
              type="text"
              placeholder="例如 25 软件工程技术 2 班"
              maxlength="128"
              :disabled="creating"
            />
            <button
              class="rounded-[var(--af-radius-sm)] bg-af-accent px-4 py-2 text-sm font-medium af-press text-af-inverse transition-colors hover:bg-af-accent-hover disabled:cursor-not-allowed disabled:opacity-60"
              type="submit"
              :disabled="creating"
            >
              {{ creating ? '创建中…' : '创建' }}
            </button>
          </form>
          <p v-if="createdHint" class="mt-2 text-xs text-af-success">{{ createdHint }}</p>
        </section>

        <p v-if="errorMsg" class="mt-3 text-xs text-af-danger" role="alert">{{ errorMsg }}</p>

        <!-- ---- List ---- -->
        <section class="mt-6">
          <h2 class="text-sm font-medium text-af-primary">我的班级</h2>

          <p v-if="loading" class="mt-3 text-xs text-af-secondary">正在加载…</p>
          <p
            v-else-if="classrooms.length === 0"
            class="af-glass mt-3 rounded-[var(--af-radius-md)] px-4 py-6 text-center text-xs text-af-secondary"
          >
            还没有班级。创建后把邀请码发给学生即可。
          </p>

          <ul v-else class="af-stagger mt-3 flex flex-col gap-3">
            <li
              v-for="room in classrooms"
              :key="room.id"
              class="af-glass af-lift rounded-[var(--af-radius-lg)] p-4"
            >
              <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <h3 class="text-sm font-medium text-af-primary">{{ room.name }}</h3>
                <span class="text-xs text-af-secondary">{{ room.member_count }} 名学生</span>
                <button
                  class="ml-auto text-[11px] text-af-muted transition-colors hover:text-af-danger"
                  type="button"
                  @click="dropClass(room)"
                >
                  删除班级
                </button>
              </div>

              <!-- The one value read aloud. See the docstring. -->
              <div class="mt-3 flex items-center gap-3">
                <span class="text-[11px] text-af-muted">邀请码</span>
                <span
                  class="rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated px-3 py-1 font-mono text-lg tracking-[0.25em] text-af-primary select-all"
                >
                  {{ room.invite_code }}
                </span>
              </div>

              <div class="mt-3">
                <button
                  class="rounded-[var(--af-radius-sm)] border border-af-border-strong bg-af-elevated px-3 py-1.5 text-xs text-af-primary transition-colors hover:bg-af-overlay"
                  type="button"
                  @click="toggleMembers(room)"
                >
                  {{ openId === room.id ? '收起名单' : '查看名单' }}
                </button>
              </div>

              <div v-if="openId === room.id" class="mt-3">
                <p v-if="membersLoading" class="text-xs text-af-secondary">正在加载…</p>
                <p v-else-if="members.length === 0" class="text-xs text-af-muted">
                  还没有学生加入。把上面的邀请码发给他们。
                </p>
                <ul v-else class="flex flex-col gap-1">
                  <li
                    v-for="member in members"
                    :key="member.user_id"
                    class="flex items-center gap-3 rounded-[var(--af-radius-sm)] px-2 py-1.5 text-xs transition-colors hover:bg-af-elevated"
                  >
                    <span class="min-w-0 truncate text-af-primary">
                      {{ member.display_name }}
                    </span>
                    <span class="shrink-0 text-af-muted">{{ member.username }}</span>
                    <button
                      class="ml-auto shrink-0 text-[11px] text-af-muted transition-colors hover:text-af-danger disabled:opacity-60"
                      type="button"
                      :disabled="busyUserId === member.user_id"
                      @click="dropMember(room, member)"
                    >
                      移出
                    </button>
                  </li>
                </ul>
              </div>
            </li>
          </ul>
        </section>
      </template>
    </div>
  </div>
</template>
