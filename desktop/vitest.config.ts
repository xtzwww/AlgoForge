/**
 * Vitest 配置（前端单元测试）。
 *
 * 只有一件事是必须的：**`@runtime-impl` 别名**。
 *
 * `runtime/index.ts` 用构建期别名选择实现（`runtime/electron.ts` 或
 * `runtime/web.ts`）——理由写在该文件的注释里。别名由各构建配置声明，
 * 所以 Vitest 也得声明一份，否则解析 `runtime/index.ts` 就失败，连带
 * 所有 import 它的模块（判题流、HTTP 客户端）都测不了。这里指向
 * `web.ts`：它不碰 Electron 桥，在 node 环境下加载最安全。
 *
 * 其余两点是刻意的克制：
 *
 * - **环境是 `node` 而不是 `jsdom`。** 首批测试覆盖的是 api 层的纯逻辑
 *   与判题流的事件时序，都不需要 DOM。等真要测组件时再装 jsdom 并配
 *   `environmentMatchGlobs`，而不是现在就把每个测试都拖进 DOM 环境里。
 * - **不开 `globals`。** 每个 spec 显式 `import { it, expect } from
 *   'vitest'`，于是 `tsconfig.web.json`（`src/renderer/src/**`）的类型
 *   检查天然覆盖测试文件，不需要给渲染进程额外加 `types: ["vitest/globals"]`。
 *   测试代码因此和被测代码共用同一套类型约束，不存在「测试里可以写
 *   `any`」的口子。
 *
 * 跑：
 *   npm run test           # 单次
 *   npm run test:watch     # 监听
 */

import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

const renderer = new URL('src/renderer/src/', import.meta.url)

export default defineConfig({
  resolve: {
    alias: {
      '@renderer': fileURLToPath(renderer),
      '@runtime-impl': fileURLToPath(new URL('src/renderer/src/runtime/web.ts', import.meta.url))
    }
  },
  test: {
    environment: 'node',
    // 只认 `*.spec.ts`。测试与被测文件同级放在 src/ 下，而不是另开
    // `tests/`：一个改名/移动过的模块，它的测试不会悄悄留在原地测不存在的代码。
    include: ['src/renderer/src/**/*.spec.ts'],
    globals: false
  }
})
