/**
 * Sidecar readiness, shared by the whole shell (PU-7).
 *
 * Three parts of the UI need the same answer to "can this app actually
 * do anything yet?": the sidebar's status dot, the content area's
 * skeleton/error state, and the retry button. They must agree, so the
 * state lives here once rather than being probed three times.
 *
 * **Why a module-level ref instead of Pinia.** AGENTS.md names Pinia as
 * the state manager, and this is the shape of thing it exists for. It is
 * not used yet because there is nothing else to store — this is the
 * first shared state in the frontend — and the decision to keep the
 * vue-router the only new runtime dependency this phase. A singleton
 * composable is the dependency-free version of the same idea, and moving
 * this into a store later is a mechanical change because every consumer
 * already goes through the functions below.
 *
 * **Why the first probe waits for the reveal signal.** In Electron the
 * window's renderer is deliberately loaded while the sidecar is still
 * booting (`src/main/index.ts` builds the window before awaiting
 * `startSidecar()`), so the main process does not publish the port and
 * token until `/health` is green. Probing on mount would therefore
 * *always* fail on desktop and leave the app permanently stuck in the
 * error state. `runtime.onReveal` is the shell's signal that the answer
 * is now knowable — the same signal the entrance animation uses.
 *
 * That also makes the two shells behave identically: the browser fires
 * reveal on the next frame (nothing is held back), so a web page with no
 * sidecar shows the error state immediately, which is the honest answer.
 */

import { computed, ref, type Ref } from 'vue'
import { initHttpClient, http } from '../api/http'
import { getEnvReport, type EnvReport, type LanguageOption } from '../api/languages'
import { runtime } from '../runtime'

/**
 * Where the startup probe has got to.
 *
 * `degraded` is separate from `failed` on purpose: the sidecar answered,
 * so nothing is broken and retrying will not help — this machine simply
 * has no usable toolchain. Collapsing the two would put a "retry" button
 * in front of a problem that retrying cannot fix.
 */
export type SidecarPhase = 'checking' | 'ready' | 'degraded' | 'failed'

const phase = ref<SidecarPhase>('checking')
const report = ref<EnvReport | null>(null)
/** Human-facing reason for a `failed` phase; empty otherwise. */
const errorMessage = ref('')

/** In-flight guard: the retry button must not stack probes. */
let inFlight: Promise<void> | null = null

/**
 * Turn whatever went wrong into a sentence a user can act on.
 *
 * Three failure modes, and they used to read identically:
 *
 * 1. "the shell never told us where the sidecar is" — a configuration
 *    problem (no `VITE_SIDECAR_URL` in the web build, or the desktop main
 *    process has not finished starting it);
 * 2. "we know where it is and it answered with an error" — the engine is
 *    connected and unhealthy, which is a *different* fix;
 * 3. "we know where it is and nothing answered" — either nothing is
 *    listening, or it accepted the connection and then died mid-request.
 *
 * Mode 3 used to say "无法连接到分析引擎" outright, which was actively
 * misleading: the commonest cause on this project is that the engine
 * **crashed** while handling the request (a `SystemExit` escaping an
 * endpoint kills uvicorn), and the process dying is what severs the
 * connection. The wording now names both possibilities instead of
 * asserting the one that is usually wrong.
 */
function describeFailure(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err)

  // Thrown by initHttpClient() when the runtime has no sidecar to point at.
  if (message.includes('Sidecar info not available')) {
    return runtime.kind === 'web'
      ? '没有配置分析引擎地址（VITE_SIDECAR_URL），网页版无法连接。'
      : '桌面端还没有拿到分析引擎的连接信息。'
  }

  const baseURL = http.defaults.baseURL ?? '(未配置)'
  const status = (err as { response?: { status?: number } }).response?.status
  if (status !== undefined) {
    return `分析引擎返回 ${status}。地址：${baseURL}`
  }

  // No response at all. Two causes, and the distinction matters because
  // only one of them is fixed by starting the engine again.
  if (message.includes('timeout')) {
    return `分析引擎没有在规定时间内响应（可能是它卡住了，而不是没启动）。地址：${baseURL}`
  }
  return `无法连接到分析引擎，或它在处理时崩溃了。地址：${baseURL}`
}

async function runProbe(): Promise<void> {
  phase.value = 'checking'
  errorMessage.value = ''

  try {
    // Configures the axios instance, and is itself the first thing that
    // needs the port and token — so it is the natural first failure point.
    await initHttpClient()
  } catch (err) {
    report.value = null
    errorMessage.value = describeFailure(err)
    phase.value = 'failed'
    return
  }

  try {
    const result = await getEnvReport()
    report.value = result
    phase.value = result.core_ready ? 'ready' : 'degraded'
  } catch (err) {
    report.value = null
    errorMessage.value = describeFailure(err)
    phase.value = 'failed'
  }
}

/**
 * Probe the sidecar, reusing an in-flight probe if there is one.
 *
 * Safe to call from anywhere; only the first call does work.
 */
export async function checkSidecar(): Promise<void> {
  if (inFlight) return inFlight
  inFlight = runProbe().finally(() => {
    inFlight = null
  })
  return inFlight
}

/**
 * Register the startup probe against the shell's reveal signal.
 *
 * Called once from `main.ts`. Deliberately not called from a component's
 * `onMounted`: the shell is remounted on nothing today, but a probe that
 * fires per mount would re-run a several-second subprocess sweep for no
 * reason, and the cache in `api/languages.ts` would hide the cost rather
 * than remove it.
 */
export function bootstrapSidecarStatus(): void {
  runtime.onReveal(() => {
    void checkSidecar()
  })

  // Recover when the engine is replaced underneath us.
  //
  // The desktop main process restarts the sidecar if it dies, and a
  // restarted sidecar listens on a **new port with a new token**. The
  // axios instance was configured once at startup, so without this the
  // app would keep calling the dead address: the sidebar would stay
  // red and every page would say "Network Error" for the rest of the
  // session, with the only fix being to quit and reopen.
  runtime.onSidecarInfoChanged((info) => {
    if (info === null) {
      // Engine gone. Say so now rather than waiting for the next page
      // request to fail — the status dot is the thing the user watches.
      phase.value = 'failed'
      errorMessage.value = '分析引擎已断开，正在尝试重新连接…'
      return
    }
    void checkSidecar()
  })
}

/**
 * Read the shared status.
 *
 * Every field is a `computed`, so consumers get a read-only view: the
 * only way to change the phase is to run a probe. `readonly(ref)` would
 * look more direct but deep-readonlys the value type, which then no
 * longer matches the API layer's own interfaces — the shell would have
 * to describe `EnvReport` as a different, read-only shape to pass it
 * around.
 */
export function useSidecarStatus(): {
  phase: Readonly<Ref<SidecarPhase>>
  errorMessage: Readonly<Ref<string>>
  report: Readonly<Ref<EnvReport | null>>
  languages: Readonly<Ref<LanguageOption[]>>
  retry: () => Promise<void>
} {
  return {
    phase: computed(() => phase.value),
    errorMessage: computed(() => errorMessage.value),
    report: computed(() => report.value),
    // Empty rather than null so templates can `v-for` without a guard.
    languages: computed<LanguageOption[]>(() => report.value?.languages ?? []),
    retry: checkSidecar
  }
}
