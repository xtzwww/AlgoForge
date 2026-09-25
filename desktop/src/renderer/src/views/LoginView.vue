<script setup lang="ts">
/**
 * LoginView — the shared sign-in page (PU-13).
 *
 * One page for both roles, because the credentials are the same kind of
 * thing: a username and a password that the teacher created. What
 * differs is only where you land afterwards, and that is decided from
 * the *server's* answer (`user.role` on the login response) rather than
 * from anything this page chose — a client-side "I am a teacher"
 * checkbox would be a claim, not a fact.
 *
 * Why there is a registration link now
 * ------------------------------------
 * This page used to say "accounts are created by a teacher, there is
 * deliberately no self-registration" and told the reader who to ask
 * instead of offering a form that did not exist. That changed on
 * 2026-09-25: the product decision is that a student can sign up, so
 * `RegisterView` exists and this page links to it. The link goes to a
 * **separate page** rather than an inline form — the two flows share a
 * shape and almost nothing else, and one card with two modes would put
 * every registration rule behind a conditional on the login path.
 *
 * What did *not* change: the account this page signs into is whatever
 * the server says it is, and the link is the only thing here that knows
 * registration exists.
 *
 * Why an already-signed-in visitor is redirected
 * ---------------------------------------------
 * Arriving here with a valid session is not an error — it happens on a
 * bookmark or a back button — and showing a login form to somebody who
 * is already logged in invites them to type credentials they do not
 * need. The redirect waits for `sessionReady`, because before the
 * startup round trip resolves the app genuinely does not know, and
 * bouncing on "unknown" would fight the restore it is waiting for.
 */

import { onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { login, sessionReady, sessionUser } from '../api/session'

const router = useRouter()

const username = ref('')
const password = ref('')
const errorMsg = ref('')
const busy = ref(false)

/** Where each role belongs once signed in. */
function homeFor(role: string): string {
  return role === 'teacher' ? 'teacher' : 'assignments'
}

/**
 * Turn a failure into a sentence the person can act on.
 *
 * A 401 is the server refusing the credential and is the only case where
 * "wrong password" is the right thing to say — `app/api/auth.py` answers
 * the same way for an unknown user, on purpose. Anything without a
 * response never reached the server, which is a different problem with a
 * different fix, and reporting it as a bad password would send someone
 * hunting for a typo that is not there.
 */
function describe(err: unknown): string {
  const status = (err as { response?: { status?: number } })?.response?.status
  if (status === 401) return '用户名或密码不正确'
  if (status === undefined) return '无法连接到分析引擎，请确认后端已启动'
  return `登录失败（HTTP ${status}）`
}

async function submit(): Promise<void> {
  if (busy.value) return
  errorMsg.value = ''

  if (!username.value.trim() || !password.value) {
    errorMsg.value = '请输入用户名和密码'
    return
  }

  busy.value = true
  try {
    const user = await login(username.value.trim(), password.value)
    await router.replace({ name: homeFor(user.role) })
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    busy.value = false
  }
}

// Already signed in? Go where that role belongs. `immediate` covers the
// case where the session was restored before this page mounted.
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
  // Nothing to fetch: the form is the whole page. The hook is kept
  // because focusing the first field is what a person expects a login
  // screen to do, and it is the only behaviour that needs the DOM.
  document.querySelector<HTMLInputElement>('input[name="username"]')?.focus()
})
</script>

<template>
  <div class="flex flex-1 items-center justify-center overflow-y-auto p-6">
    <div class="w-full max-w-sm">
      <div class="af-glass rounded-[var(--af-radius-lg)] p-6">
        <h1 class="text-lg font-semibold text-af-primary">登录</h1>
        <p class="mt-1.5 text-xs leading-relaxed text-af-secondary">
          使用老师分配的账号，或者
          <RouterLink class="text-af-accent hover:text-af-accent-hover" :to="{ name: 'register' }">
            自己注册一个
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
              autocomplete="current-password"
              :disabled="busy"
            />
          </label>

          <p
            v-if="errorMsg"
            class="rounded-[var(--af-radius-sm)] border border-af-danger/40 bg-af-elevated px-3 py-2 text-xs text-af-danger"
            role="alert"
          >
            {{ errorMsg }}
          </p>

          <button
            class="rounded-[var(--af-radius-sm)] bg-af-accent px-4 py-2 text-sm font-medium af-press text-af-inverse transition-colors hover:bg-af-accent-hover disabled:cursor-not-allowed disabled:opacity-60"
            type="submit"
            :disabled="busy"
          >
            {{ busy ? '登录中…' : '登录' }}
          </button>
        </form>
      </div>

      <p class="mt-4 text-center text-[11px] leading-relaxed text-af-muted">
        学生登录后进入「我的作业」，教师登录后进入「学情看板」。
      </p>
    </div>
  </div>
</template>
