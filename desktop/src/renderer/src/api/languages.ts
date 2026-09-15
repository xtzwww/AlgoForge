/**
 * Language catalogue client (PU-17, extended in PU-7).
 *
 * The list of languages the editor may offer — and which of them this
 * machine can actually build and run — comes from the sidecar's
 * `/api/v1/env-check` (PU-3). Fetching it is deliberately *not* a
 * hardcoded array in this file:
 *
 *   - `LanguageSpec` in `sidecar/app/sandbox/languages.py` owns what a
 *     language *is* (its id, its Monaco id, its starter template). A
 *     second copy here could not be kept honest against it — the Java
 *     template in particular has to stay within Java 8 syntax, because
 *     that is the only JDK on this machine.
 *   - Availability is a property of the machine, not of the app, so it
 *     can only be answered by the sidecar that will do the compiling.
 *
 * The report costs the sidecar several subprocess launches, so it is
 * fetched once and cached for the session: a toolchain cannot appear or
 * disappear while the window is open, and re-fetching on every render
 * would be pure latency.
 *
 * PU-7 note — why this module now exposes the whole report and not just
 * the language array. The shell's status indicator needs `core_ready`,
 * and the tempting shortcut is to compute it here as
 * `languages.some(l => l.available)`. That happens to be exactly what
 * `probe_all()` does today, which is precisely the problem: it is a
 * second copy of a server-side predicate, free to drift the moment the
 * sidecar widens its definition of "core" (to include the database, or
 * the sandbox temp directory). Passing the server's own answer through
 * is the only version of this that stays true.
 */

import { http } from './http'

/** One selectable language, as reported by `GET /api/v1/env-check`. */
export interface LanguageOption {
  /** Canonical id sent back to the sidecar, e.g. `"cpp"`. */
  id: string
  /** Human-facing name, e.g. `"C++"`. */
  display_name: string
  /** Monaco's language id, used for syntax highlighting. */
  monaco_language: string
  /** Starter code inserted into an empty buffer. */
  default_template: string
  /** False when this machine has no usable toolchain for it. */
  available: boolean
  version: string | null
  /** Why the language is unusable, plus which setting to change. */
  detail: string | null
}

/**
 * The sidecar's full environment report.
 *
 * `core_ready` is the server's verdict on whether the edit → judge loop
 * can work at all; the shell's status indicator renders it directly.
 * `database` and `sandbox_tmp` are also present but not consumed yet —
 * the shell only needs the one flag, and typing the rest invites code
 * that reads fields nobody has decided the meaning of.
 */
export interface EnvReport {
  languages: LanguageOption[]
  core_ready: boolean
}

let cached: EnvReport | null = null
let inFlight: Promise<EnvReport> | null = null

/**
 * Fetch the environment report, reusing the previous result if there is
 * one.
 *
 * Concurrent callers share a single request, so a component that mounts
 * while the app is still starting up does not trigger a second probe run.
 *
 * A **failed** call caches nothing, which is what makes retrying work
 * without a cache-busting API: the caller simply calls again. The one
 * case that would need an explicit reset — re-probing after a *successful*
 * report — cannot arise, because a toolchain cannot change while the
 * window is open.
 *
 * @throws When the sidecar is unreachable. Callers are expected to render
 *   an error state rather than guess a language list — offering languages
 *   we cannot verify would just move the failure to judge time.
 */
export async function getEnvReport(): Promise<EnvReport> {
  if (cached) return cached
  if (inFlight) return inFlight

  inFlight = http
    .get<EnvReport>('/api/v1/env-check')
    .then((res) => {
      cached = res.data
      return cached
    })
    .finally(() => {
      inFlight = null
    })

  return inFlight
}

/**
 * The language catalogue alone, for callers that only need to render
 * pickers. Thin wrapper over {@link getEnvReport} so both share one
 * request and one cache.
 *
 * @returns Every registered language, in the registry's display order.
 */
export async function getLanguageOptions(): Promise<LanguageOption[]> {
  return (await getEnvReport()).languages
}

/**
 * The language a fresh editor should start in.
 *
 * Prefers `"cpp"` when it is usable, matching the sidecar's own
 * `DEFAULT_LANGUAGE` and the pre-PU-17 behaviour of the practice page.
 * Falls back to the first usable language so a machine without a C++
 * toolchain still gets a working editor instead of a disabled default.
 *
 * @param options Catalogue from {@link getLanguageOptions}.
 * @returns The chosen language, or `null` when nothing is usable.
 */
export function pickDefaultLanguage(options: LanguageOption[]): LanguageOption | null {
  const preferred = options.find((o) => o.id === 'cpp' && o.available)
  if (preferred) return preferred
  return options.find((o) => o.available) ?? null
}
