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
 * first shared state in the frontend — and PHASE-UX.md decision 3 makes
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
 * The two failure modes are genuinely different and used to be
 * indistinguishable in the console: "the shell never told us where the
 * sidecar is" (a configuration problem — no `VITE_SIDECAR_URL` in the
 * web build, or the desktop main process has not finished starting it)
 * versus "we know where it is and it did not answer" (a connectivity
 * problem). Only the second is worth offering a retry for.
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
  return `无法连接到分析引擎。地址：${baseURL}`
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
