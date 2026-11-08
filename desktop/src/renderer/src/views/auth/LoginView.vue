<script setup lang="ts">
/**
 * LoginView — the shared sign-in page (PU-13).
 *
 * One page for both roles, because the credentials are the same kind of
 * thing: a username and a password that the teacher created. What
 * differs is only where you land afterwards.
 *
 * The page now (2026-09-25, third round) ties the entry to the identity:
 * the 学生/老师 toggle still exists, but switching to the *other side's*
 * identity is a pointer — it sends the visitor to that side's sign-in URL,
 * and a sign-in whose account role does not match the entry is refused
 * with a message that says where that account belongs. Electron (`both`)
 * has no entry and is exempt. This is the same boundary the router guard
 * applies to sessions that arrive via refresh or a deep link.
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
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { NButton, NInput } from 'naive-ui'
import AuthLayout from './AuthLayout.vue'
import { login, logout, sessionReady, sessionUser } from '../../api/session'
import {
  defaultIntent,
  entryMismatch,
  entryUrlForIntent,
  homeForIntent,
  landingName,
  mismatchMessage,
  type EntryMismatch,
  type LoginIntent
} from '../../router/landing'
import { appMode } from '../../runtime/mode'
import { goTo } from '../../runtime/navigation'

const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const errorMsg = ref('')
const busy = ref(false)

/**
 * 使用者在这一页表明的身份（2026-09-25 第二轮；第三轮起入口即身份）。
 * 默认跟随入口（教师端默认老师、学生端默认学生）。切换不再只是选落点：
 * 换到**不属于当前入口**的身份时，直接整页送去那端的登录页——学生端网址
 * 点「老师」=「去教师网址登录」，互登另一端在本页就不再发生。
 */
const intent = ref<LoginIntent>(defaultIntent(appMode))

function setIntent(next: LoginIntent): void {
  intent.value = next
  // 入口即身份：跨端的身份切换是「指路」，不是「在本页换个身份登录」。
  if (
    (appMode === 'teacher' && next === 'student') ||
    (appMode === 'student' && next === 'teacher')
  ) {
    goTo(entryUrlForIntent(next, 'login'))
  }
}

/**
 * 按当前意图落点：目标端路由在本地路由表里就直接跳，不在（学生入口
 * 选老师、教师入口选学生）就整页跳到另一端入口——同源共享会话 token，
 * 跳转后直接以该身份进入。
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
 * Where each role lands once signed in, on the *current side*
 * (2026-09-25). A teacher account signing in on the student entry has no
 * console route to land on — `landingName` falls back for exactly that
 * case; see its docstring in `router/landing.ts`.
 */
function homeFor(role: string): string {
  return landingName(appMode, role)
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
    // 入口即身份（2026-09-25 第三轮）：学生端网址不收教师账号、教师端
    // 网址不收学生账号。凭据有效但入口不对 → 提示去哪一端，并立刻清掉
    // 刚签发的会话，避免已登录态把页面带进另一端。
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
  // 全局守卫拦下的互登（刷新/深链/整页跳转带来的会话）会带着
  // `?entryErr=` 回到这里——把那份提示原样显示出来。
  const entryErr = route.query.entryErr
  if (typeof entryErr === 'string' && entryErr.length) {
    errorMsg.value = mismatchMessage(entryErr as EntryMismatch)
  }
  // Nothing to fetch: the form is the whole page. The hook is kept
  // because focusing the first field is what a person expects a login
  // screen to do, and it is the only behaviour that needs the DOM.
  document.querySelector<HTMLInputElement>('input[name="username"]')?.focus()
})
</script>

<template>
  <AuthLayout>
    <template #switcher>
      <!--
        Who are you signing in as. Picks the landing, not the account:
        the server still answers with the real role (see the docstring).
      -->
      <div
        class="mb-4 flex rounded-[var(--af-radius-sm)] border border-af-border bg-af-elevated p-0.5"
        role="group"
        aria-label="以什么身份登录"
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
    </template>

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
        <NInput
          v-model:value="username"
          type="text"
          name="username"
          autocomplete="username"
          :disabled="busy"
          placeholder=""
        />
      </label>

      <label class="flex flex-col gap-1.5">
        <span class="text-xs text-af-secondary">密码</span>
        <NInput
          v-model:value="password"
          type="password"
          name="password"
          autocomplete="current-password"
          :disabled="busy"
          placeholder=""
        />
      </label>

      <p
        v-if="errorMsg"
        class="rounded-[var(--af-radius-sm)] border border-af-danger/40 bg-af-elevated px-3 py-2 text-xs text-af-danger"
        role="alert"
      >
        {{ errorMsg }}
      </p>

      <NButton
        attr-type="submit"
        type="primary"
        block
        :loading="busy"
      >
        {{ busy ? '登录中…' : '登录' }}
      </NButton>
    </form>

    <template #footer>
      {{
        appMode === 'teacher'
          ? '教师账号在这里登录。学生账号请点「学生」前往学生网址（index.web.html）。'
          : '学生账号在这里登录。教师账号请点「老师」前往教师网址（teacher.web.html）。'
      }}
    </template>
  </AuthLayout>
</template>
