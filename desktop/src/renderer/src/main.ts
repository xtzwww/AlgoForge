import './assets/main.css'

import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { bootstrapSidecarStatus, checkSidecar } from './composables/useSidecarStatus'
import { restoreSession } from './api/session'
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

createApp(App).use(router).mount('#app')
