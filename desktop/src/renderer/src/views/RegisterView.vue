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
 * Offer a role *field*. There is no role input and no free-form "I am a
 * teacher" claim, but there is now (2026-09-25, second round) a
 * 学生/老师 toggle: the person picks which side they are signing up for,
 * and the page asks the server for exactly that role. The toggle is a
 * request, not a fact — the server keeps its own whitelist + switch
 * (`app/registration.py`): only the literal `teacher` can become a
 * teacher, and only while the deployment has that switch on; everything
 * else comes back a student. A role control the server did not own
 * would be an exploit the day it stopped refusing; the whitelist is
 * what keeps this one honest.
 *
 * Entry is identity (third round, 2026-09-25): switching the toggle to
 * the *other side's* identity sends the visitor to that side's register
 * URL, and a sign-up that comes back with the wrong role for this entry
 * (the server downgraded it — switch off, whitelist miss) is refused
 * with a message pointing at where that account belongs. Electron
 * (`both`) has no entry and is exempt.
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
import { NButton, NInput } from 'naive-ui'
import { register, logout, sessionReady, sessionUser } from '../api/session'
import {
  defaultIntent,
  entryMismatch,
  entryUrlForIntent,
  homeForIntent,
  landingName,
  mismatchMessage,
  type LoginIntent
} from '../router/landing'
import { appMode } from '../runtime/mode'
import { goTo } from '../runtime/navigation'

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

/**
 * Which side this page asks the server to create (2026-09-25, second
 * round). Defaults to the entry (teacher entry → teacher), switchable on
 * the page. The server's whitelist + switch stays the final word.
 */
const intent = ref<LoginIntent>(defaultIntent(appMode))

function setIntent(next: LoginIntent): void {
  intent.value = next
  // 入口即身份：跨端的身份切换是「指路」，直接送去那端的注册页。
  if (
    (appMode === 'teacher' && next === 'student') ||
    (appMode === 'student' && next === 'teacher')
  ) {
    goTo(entryUrlForIntent(next, 'register'))
  }
}

/** Whether the person asked for a teacher account — drives the copy. */
const isTeacherIntent = computed<boolean>(() => intent.value === 'teacher')

/** The role this page will ask the server for. */
const requestedRole = computed<'student' | 'teacher'>(() => intent.value)

/**
 * Land per the picked intent: same-entry route when available, otherwise
 * a full page hop to the other entry (same-origin session carries over).
 */
function navigateHome(i: LoginIntent): void {
  const routeName = homeForIntent(i, appMode)
  if (routeName) {
    void router.replace({ name: routeName })
  } else {
    goTo(entryUrlForIntent(i))
  }
}

/**
 * Where an already-signed-in visitor goes, on the *current side*
 * (2026-09-25). Mirrors `LoginView` and uses the same `landingName`
 * fallback: a student account registered on the teacher entry has no
 * student route to land on, and lands on the console, whose own 403
 * explanation then talks (see `router/landing.ts`).
 */
function homeFor(role: string): string {
  return landingName(appMode, role)
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
    const user = await register(
      name,
      password.value,
      displayName.value.trim(),
      requestedRole.value
    )
    // The server may answer with something other than what was asked for
    // (whitelist/switch). 入口即身份：注册得到的账号不属于当前入口时
    // （例如教师端网址在开关关闭时被降级成学生号），提示去哪一端，
    // 并清掉刚签发的会话。
    const mismatch = entryMismatch(appMode, user.role)
    if (mismatch) {
      errorMsg.value = mismatchMessage(mismatch)
      await logout()
      await router.replace({ name: 'login' })
      return
    }
    navigateHome(intent.value)
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
        <!--
          Who are you signing up as. Asks the server for the picked role;
          the server's whitelist + switch is the final word (docstring).
        -->
        <div
          class="mb-4 flex rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated p-0.5"
          role="group"
          aria-label="注册成什么账号"
        >
          <NButton
            class="flex-1"
            size="small"
            quaternary
            :type="intent === 'student' ? 'primary' : 'default'"
            :aria-pressed="intent === 'student' ? 'true' : 'false'"
            @click="setIntent('student')"
          >
            学生
          </NButton>
          <NButton
            class="flex-1"
            size="small"
            quaternary
            :type="intent === 'teacher' ? 'primary' : 'default'"
            :aria-pressed="intent === 'teacher' ? 'true' : 'false'"
            @click="setIntent('teacher')"
          >
            老师
          </NButton>
        </div>

        <h1 class="text-lg font-semibold text-af-primary">注册</h1>
        <p v-if="isTeacherIntent" class="mt-1.5 text-xs leading-relaxed text-af-secondary">
          自己创建一个教师账号。注册成功后就能建班、布置作业、查看全班学情。已有账号？直接
          <RouterLink class="text-af-accent hover:text-af-accent-hover" :to="{ name: 'login' }">
            去登录
          </RouterLink>
          。
        </p>
        <p v-else class="mt-1.5 text-xs leading-relaxed text-af-secondary">
          自己创建一个学生账号。如果老师已经给你发过账号，直接
          <RouterLink class="text-af-accent hover:text-af-accent-hover" :to="{ name: 'login' }">
            去登录
          </RouterLink>
          。
        </p>

        <form class="mt-5 flex flex-col gap-4" @submit.prevent="submit">
          <label class="flex flex-col gap-1.5">
            <span class="text-xs text-af-secondary">用户名</span>
            <NInput
              v-model:value="username"
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
            <NInput
              v-model:value="displayName"
              type="text"
              name="display-name"
              autocomplete="name"
              placeholder="老师看到的名字，留空就用用户名"
              :disabled="busy"
            />
          </label>

          <label class="flex flex-col gap-1.5">
            <span class="text-xs text-af-secondary">密码</span>
            <NInput
              v-model:value="password"
              type="password"
              name="password"
              autocomplete="new-password"
              :placeholder="`至少 ${PASSWORD_MIN} 位`"
              :disabled="busy"
            />
          </label>

          <label class="flex flex-col gap-1.5">
            <span class="text-xs text-af-secondary">确认密码</span>
            <NInput
              v-model:value="confirm"
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

          <NButton
            attr-type="submit"
            type="primary"
            block
            :loading="busy"
            :disabled="busy"
          >
            {{ busy ? '注册中…' : '注册并进入' }}
          </NButton>
        </form>
      </div>

      <p class="mt-4 text-center text-[11px] leading-relaxed text-af-muted">
        <template v-if="isTeacherIntent">
          这里注册得到的是教师账号，可以建班、布置作业、查看全班学情。学生账号请到学生网址（index.web.html）注册，或由老师在「班级管理」里添加。
        </template>
        <template v-else>
          这里注册得到的是学生账号。教师账号请到教师网址（teacher.web.html）注册。
        </template>
      </p>
    </div>
  </div>
</template>
