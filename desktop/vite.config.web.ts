/**
 * Web build target (PU-5).
 *
 * A standalone Vite config that builds the *same* renderer source as
 * `electron.vite.config.ts` into a browser-loadable bundle. Two things
 * differ, and only two:
 *
 * 1. **`@runtime-impl` points at `runtime/web.ts`** instead of
 *    `runtime/electron.ts`. This is the whole reason the web build does
 *    not ship the Electron bridge — see `runtime/index.ts`.
 * 2. **The entry is `index.web.html`**, which has no CSP meta tag. A
 *    served app gets its CSP from response headers, which is also the
 *    only place that can name the sidecar's real origin. See the comment
 *    in that file.
 *
 * Everything else — Tailwind, the Monaco worker setup, the `@renderer`
 * alias — is kept identical to the Electron renderer config on purpose.
 * A divergence here would mean the two targets render differently, which
 * is exactly the class of bug this arrangement is meant to prevent.
 *
 * Usage:
 *   npm run dev:web     # dev server on 5173, needs a running sidecar
 *   npm run build:web   # static bundle in dist-web/
 */

import { resolve } from 'path'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

/** Must match `server.port` below, and the sidecar's CORS_ALLOW_ORIGINS. */
const DEV_PORT = 5173

export default defineConfig(({ mode }) => {
  // `root` is src/renderer, so Vite would look for `.env` files there.
  // envDir moves that back to desktop/, where `.env.web` lives.
  const env = loadEnv(mode, resolve('.'), 'VITE_')

  // `.env.web.example` sets VITE_TARGET=web. Nothing branches on it —
  // the alias above decides the implementation — but if someone copies
  // the wrong env file the mismatch is worth catching here rather than
  // diagnosing later from a bundle that behaves like the other target.
  if (env.VITE_TARGET && env.VITE_TARGET !== 'web') {
    throw new Error(
      `VITE_TARGET is ${JSON.stringify(env.VITE_TARGET)} but this is the web ` +
        `build. Check which .env file was loaded (expected .env.web).`
    )
  }

  return {
    root: resolve('src/renderer'),
    envDir: resolve('.'),
    plugins: [vue(), tailwindcss()],
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src'),
        '@runtime-impl': resolve('src/renderer/src/runtime/web.ts')
      }
    },
    build: {
      outDir: resolve('dist-web'),
      emptyOutDir: true,
      rollupOptions: {
        input: {
          // The web split (2026-09-25): one entry per side, sharing the
          // same renderer bundle. `src/main.ts` picks the side from the
          // entry file name (runtime/mode.ts), so the student address
          // resolves only student routes and the teacher address only
          // teacher routes.
          index: resolve('src/renderer/index.web.html'),
          teacher: resolve('src/renderer/teacher.web.html')
        }
      }
    },
    server: {
      port: DEV_PORT,
      // Fail rather than silently moving to 5174: the sidecar's CORS
      // allowlist names this exact port, so a different one would 403
      // and look like a sidecar problem.
      strictPort: true
    },
    optimizeDeps: {
      include: ['monaco-editor']
    },
    worker: {
      format: 'es'
    }
  }
})
