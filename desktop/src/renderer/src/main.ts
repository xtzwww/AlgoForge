import './assets/main.css'
// 教师端五个面板共用的皮肤（2026-09-26 拆分时抽出）。刻意不并进
// `main.css`：这些类只在教师端用，学生端不必背这份重量。见该文件的文档。
import './assets/teacher.css'

import { createApp, watch } from 'vue'
import App from './App.vue'
import { router } from './router'
import { installGuards } from './router/guards'
import { bootstrapSidecarStatus, checkSidecar } from './composables/useSidecarStatus'
import { logout, restoreSession, sessionReady, sessionUser } from './api/session'
import { entryMismatch } from './router/landing'
import { appMode } from './runtime/mode'
import { runtime } from './runtime'

// PU-2: entrance animation, driven by the shell rather than by load.
// Under Electron the window stays hidden until the sidecar is healthy,
// so a load-time CSS animation would finish before anyone could see it;
// in the browser there is nothing to hold back and the runtime fires
// this on the next frame. Either way the app registers the class and
// the shell decides when it lands.
runtime.onReveal(() => {
  document.documentElement.classList.add('af-revealed')
})

// PU-7: the first sidecar probe, for the same reason. Under Electron the
// renderer is loaded *before* the main process publishes the sidecar's
// port and token, so probing at mount would always fail and leave the
// app stuck on the error screen. See useSidecarStatus.ts.
bootstrapSidecarStatus()

// B1: re-establish any stored session.
//
// Deliberately not awaited — the student-facing pages work while logged
// out, so blocking the mount on a round trip would make every screen
// wait on a credential only one of them needs. The teacher page reads
// `sessionReady` and shows a placeholder until this lands.
//
// It does have to run *after* `checkSidecar()`, though, because that
// probe is what calls `initHttpClient()` and therefore what gives
// `http` a base URL. Firing the request first would send it to the
// page's own origin — a 404 in the browser, a silent failure under
// Electron — which would then be read as "the stored token is dead".
// `checkSidecar()` is idempotent (it reuses the in-flight probe), so
// awaiting it here costs nothing it was not already going to spend.
void (async () => {
  await checkSidecar()
  await restoreSession()
})()

// B2: entry is identity (2026-09-25, third round) + auth (2026-10-04).
// Two layers do the entry redirect: the router guard
// (`router/guards/entry.ts`) catches every *later* navigation, and the
// watcher below catches the restored session itself — the page's first
// navigation happens before `restoreSession()` answers, so the guard
// would see `sessionReady` false and let it through. Together no path
// lands a teacher account on the student entry or vice versa; Electron
// (`both`) is exempt.
//
// `installGuards` installs entry **then** auth; the order matters, see
// its docstring in `router/guards/index.ts`.
installGuards(router)

watch(
  () => [sessionReady.value, sessionUser.value] as const,
  async ([ready, user]) => {
    if (!ready || !user) return
    const mismatch = entryMismatch(appMode, user.role)
    if (!mismatch) return
    await logout()
    await router.replace({ name: 'login', query: { entryErr: mismatch } })
  }
)

createApp(App).use(router).mount('#app')
