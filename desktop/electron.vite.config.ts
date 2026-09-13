import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {},
  preload: {
    build: {
      rollupOptions: {
        input: {
          index: resolve('src/preload/index.ts'),
          // PU-2: the splash window gets its own minimal bridge (status
          // text + fade-out only) instead of the full app preload.
          splash: resolve('src/preload/splash.ts')
        }
      }
    }
  },
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    // PU-0: Tailwind v4 (CSS-first config — tokens live in assets/tokens.css,
    // no tailwind.config.js required).
    plugins: [vue(), tailwindcss()],
    build: {
      rollupOptions: {
        input: {
          index: resolve('src/renderer/index.html'),
          // PU-2: separate entry for the splash so it can paint
          // immediately without pulling in the Monaco-heavy main bundle.
          splash: resolve('src/renderer/splash.html')
        }
      }
    },
    optimizeDeps: {
      include: ['monaco-editor']
    },
    worker: {
      format: 'es'
    }
  }
})
