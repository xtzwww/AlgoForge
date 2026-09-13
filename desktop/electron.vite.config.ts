import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    // PU-0: Tailwind v4 (CSS-first config — tokens live in assets/tokens.css,
    // no tailwind.config.js required).
    plugins: [vue(), tailwindcss()],
    optimizeDeps: {
      include: ['monaco-editor']
    },
    worker: {
      format: 'es'
    }
  }
})
