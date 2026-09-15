/**
 * Runtime selection.
 *
 * The concrete implementation is chosen **at build time**, by aliasing
 * the `@runtime-impl` specifier:
 *
 * - `electron.vite.config.ts` maps it to `./electron`
 * - `vite.config.web.ts` maps it to `./web`
 *
 * Why not `import.meta.env.VITE_TARGET === 'electron' ? a : b`? Because
 * that is a *run-time* choice over *static* imports: both modules are
 * pulled into the graph, and whether the unused one is dropped depends
 * on the bundler proving it has no side effects. When that proof fails —
 * and it is not something a reader can verify by looking at this file —
 * the browser bundle ships the Electron bridge code, which is exactly
 * the outcome the web target exists to avoid. Aliasing makes the
 * question moot: the other module is never in the graph, so there is
 * nothing to shake out.
 *
 * The cost is that both build configs must declare the alias, and a new
 * target must remember to. That is a visible, one-line failure — a build
 * that cannot resolve `@runtime-impl` — rather than a silent one.
 *
 * Anything the UI wants to branch on should come from
 * `runtime.capabilities` or `runtime.kind`, not from a build flag: those
 * are answered by the implementation itself, so there is one place to
 * change when a shell gains or loses an ability.
 */

import { runtime } from '@runtime-impl'

export { runtime }
export type { Runtime, RuntimeCapabilities, SidecarInfo } from './types'
