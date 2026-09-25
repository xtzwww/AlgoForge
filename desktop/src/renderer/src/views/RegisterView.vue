<script setup lang="ts">
/**
 * RegisterView — self-service sign-up (2026-09-25).
 *
 * A page of its own rather than a tab or a modal on `LoginView`. The two
 * flows share a shape (username, password, submit) and almost nothing
 * else: signing in is one round trip with two failure modes, signing up
 * has a second password field, four more failure modes, and a rule about
 * what it produces. Putting them in one card would mean one component
 * with two modes, and every conditional in it would be a place for the
 * login path to pick up a registration rule by accident.
 *
 * What this page must never do
 * ----------------------------
 * Offer a choice of role. There is no role field and there is no "I am a
 * teacher" checkbox, because the server decides the role and ignores
 * anything a request says about it (`app/registration.py`). A control
 * here would be a lie in the interface even if the server refused it —
 * and if the server ever stopped refusing it, the control would be the
 * exploit.
 *
 * Why the client validates at all, given the server does
 * -----------------------------------------------------
 * The floors are duplicated here only to answer *before* a round trip
 * about the two mistakes people actually make (too-short password, and
 * two passwords that differ). The server remains the authority: every
 * message it returns is shown verbatim, because it knows things this
 * page cannot — that the username is taken, that the machine has
 * self-registration switched off, that too many accounts were created
 * from here recently.
 *
 * Why an already-signed-in visitor is redirected
 * ---------------------------------------------
 * Same reason as `LoginView`, and the same `sessionReady` gate: before
 * the startup round trip resolves the app genuinely does not know, and
 * bouncing on "unknown" would fight the restore it is waiting for.
 */

import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { register, sessionReady, sessionUser } from '../api/session'

const router = useRouter()

const username = ref('')
const displayName = ref('')
const password = ref('')
const confirm = ref('')
const errorMsg = ref('')
const busy = ref(false)

/**
 * Client-side mirrors of the server's floors.
 *
 * Kept as constants next to the copy that quotes them rather than
 * imported from anywhere: the server's values live in Python, and a
 * build step to share them would be more machinery than two numbers
 * are worth. They are checked here only to save a round trip; see the
 * docstring.
 */
const USERNAME_MIN = 2
const USERNAME_MAX = 32
const PASSWORD_MIN = 6

/** Where each role belongs once signed in. Mirrors `LoginView`. */
function homeFor(role: string): string {
  return role === 'teacher' ? 'teacher' : 'assignments'
}

/** The first thing wrong with what has been typed, or `''`. */
const localProblem = computed<string>(() => {
  const name = username.value.trim()
  if (!name) return ''
  if (name.length < USERNAME_MIN || name.length > USERNAME_MAX) {
    return `用户名长度需在 ${USERNAME_MIN}–${USERNAME_MAX} 个字符之间`
  }
  if (password.value && password.value.length < PASSWORD_MIN) {
    return `密码至少 ${PASSWORD_MIN} 位`
  }
  if (confirm.value && confirm.value !== password.value) {
    return '两次输入的密码不一致'
  }
  return ''
})

/**
 * Turn a failure into a sentence the person can act on.
 *
 * The server's `detail` wins whenever it sent one: those messages are
 * written for this form and are more specific than anything inferable
 * from a status code (they name which field failed, and how long to wait
 * after a 429). The status codes are the fallback, for a body that is
 * missing or is not the shape this expects.
 */
function describe(err: unknown): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response
  const detail = typeof response?.data?.detail === 'string' ? response.data.detail : ''

  switch (response?.status) {
    case 409:
      return detail || '这个用户名已经被占用了，换一个试试'
    case 429:
      return detail || '注册过于频繁，请稍后再试'
    case 403:
      return detail || '本机已关闭自助注册，请联系老师创建账号'
    case 400:
      return detail || '用户名或密码不符合要求'
    case undefined:
      // Never reached the server. A different problem with a different
      // fix, and reporting it as a bad password would send someone
      // hunting for a typo that is not there.
      return '无法连接到分析引擎，请确认后端已启动'
    default:
      return `注册失败（HTTP ${response?.status}）`
  }
}

async function submit(): Promise<void> {
  if (busy.value) return
  errorMsg.value = ''

  const name = username.value.trim()
  if (!name || !password.value) {
    errorMsg.value = '请填写用户名和密码'
    return
  }
  if (name.length < USERNAME_MIN || name.length > USERNAME_MAX) {
    errorMsg.value = `用户名长度需在 ${USERNAME_MIN}–${USERNAME_MAX} 个字符之间`
    return
  }
  if (password.value.length < PASSWORD_MIN) {
    errorMsg.value = `密码至少 ${PASSWORD_MIN} 位`
    return
  }
  if (confirm.value !== password.value) {
    errorMsg.value = '两次输入的密码不一致'
    return
  }

  busy.value = true
  try {
    const user = await register(name, password.value, displayName.value.trim())
    // Registered accounts are always students, so this lands on the
    // assignments page — but it reads the role off the response rather
    // than assuming, so the day the server produces something else the
    // navigation is still right.
    await router.replace({ name: homeFor(user.role) })
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    busy.value = false
  }
}

// Already signed in? Go where that role belongs. See the docstring.
watch(
  () => [sessionReady.value, sessionUser.value] as const,
  ([ready, user]) => {
    if (ready && user) {
      void router.replace({ name: homeFor(user.role) })
    }
  },
  { immediate: true }
)

onMounted(() => {
  document.querySelector<HTMLInputElement>('input[name="username"]')?.focus()
})
</script>

<template>
  <div class="flex flex-1 items-center justify-center overflow-y-auto p-6">
    <div class="w-full max-w-sm">
      <div class="af-glass rounded-[var(--af-radius-lg)] p-6">
        <h1 class="text-lg font-semibold text-af-primary">注册</h1>
        <p class="mt-1.5 text-xs leading-relaxed text-af-secondary">
          自己创建一个学生账号。如果老师已经给你发过账号，直接
          <RouterLink class="text-af-accent hover:text-af-accent-hover" :to="{ name: 'login' }">
            去登录
          </RouterLink>
          。
        </p>

        <form class="mt-5 flex flex-col gap-4" @submit.prevent="submit">
          <label class="flex flex-col gap-1.5">
            <span class="text-xs text-af-secondary">用户名</span>
            <input
              v-model="username"
              class="w-full rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated px-3 py-2 text-sm text-af-primary transition-colors placeholder:text-af-muted focus:border-af-accent focus:outline-none disabled:opacity-60"
              type="text"
              name="username"
              autocomplete="username"
              placeholder="登录时使用，建议用学号"
              :disabled="busy"
            />
          </label>

          <label class="flex flex-col gap-1.5">
            <span class="text-xs text-af-secondary">
              姓名
              <span class="text-af-muted">（选填）</span>
            </span>
            <input
              v-model="displayName"
              class="w-full rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated px-3 py-2 text-sm text-af-primary transition-colors placeholder:text-af-muted focus:border-af-accent focus:outline-none disabled:opacity-60"
              type="text"
              name="display-name"
              autocomplete="name"
              placeholder="老师看到的名字，留空就用用户名"
              :disabled="busy"
            />
          </label>

          <label class="flex flex-col gap-1.5">
            <span class="text-xs text-af-secondary">密码</span>
            <input
              v-model="password"
              class="w-full rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated px-3 py-2 text-sm text-af-primary transition-colors placeholder:text-af-muted focus:border-af-accent focus:outline-none disabled:opacity-60"
              type="password"
              name="password"
              autocomplete="new-password"
              :placeholder="`至少 ${PASSWORD_MIN} 位`"
              :disabled="busy"
            />
          </label>

          <label class="flex flex-col gap-1.5">
            <span class="text-xs text-af-secondary">确认密码</span>
            <input
              v-model="confirm"
              class="w-full rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated px-3 py-2 text-sm text-af-primary transition-colors placeholder:text-af-muted focus:border-af-accent focus:outline-none disabled:opacity-60"
              type="password"
              name="confirm-password"
              autocomplete="new-password"
              :disabled="busy"
            />
          </label>

          <!--
            One message slot, not two. `errorMsg` is what the server
            said; `localProblem` is what this page can tell before
            asking. Showing both at once would put two red sentences
            under one form and leave the reader to work out which one is
            the real answer.
          -->
          <p
            v-if="errorMsg"
            class="rounded-[var(--af-radius-sm)] border border-af-danger/40 bg-af-elevated px-3 py-2 text-xs text-af-danger"
            role="alert"
          >
            {{ errorMsg }}
          </p>
          <p v-else-if="localProblem" class="text-xs text-af-warning">{{ localProblem }}</p>

          <button
            class="af-press rounded-[var(--af-radius-sm)] bg-af-accent px-4 py-2 text-sm font-medium text-af-inverse transition-colors hover:bg-af-accent-hover disabled:cursor-not-allowed disabled:opacity-60"
            type="submit"
            :disabled="busy"
          >
            {{ busy ? '注册中…' : '注册并进入' }}
          </button>
        </form>
      </div>

      <p class="mt-4 text-center text-[11px] leading-relaxed text-af-muted">
        注册得到的是学生账号。要建班、布置作业，需要老师账号 —— 请让老师登录后在「班级管理」里操作。
      </p>
    </div>
  </div>
</template>
