# Phase UX — 前端体验、多语言与题库预准备

> **本阶段定位**：Phase 1（纯工程基建）已完成的 P1-0 ~ P1-23 留下了三个断层——
> 前端视觉粗糙、启动有黑框、题目硬编码。本阶段不碰 AI，专注把**产品外壳**做成
> 能交付的样子，并为 Phase 2（Agent，由你手写）准备好所有**输入数据**。
>
> **阶段编号**：`PU-0` ~ `PU-17`（PU = Phase UX）。
> **纪律**：与 VIBECODING.md 一致——一次一个切片、每步可验证、不改无关代码。
> **先读**：`AGENTS.md`（技术红线）、`VIBECODING.md`（切片规范）。

---

## 〇、阶段动机与断层清单

Phase 1 结束时，后端已有 15 个路由、4 个前端页面，但存在以下**产品级断层**：

| # | 断层 | 证据（现状代码） | 本阶段切片 |
|---|---|---|---|
| 1 | 启动有两个黑框 | `start.ps1` 用 `Start-Process powershell.exe` 拉 sidecar；`start.bat` 同理。用户看到 cmd + PowerShell 两个控制台窗口 | PU-1 ~ PU-3 |
| 2 | 无启动动画 | `main/index.ts` 用 `show: false` + `ready-to-show`，白屏直出，无品牌感 | PU-2 |
| 3 | 只有桌面端 | `desktop/` 是唯一入口；`http.ts` 依赖 `window.algoforge`（Electron 注入），浏览器打开必炸 | PU-4 ~ PU-6 |
| 4 | 布局丑、无设计系统 | `App.vue` 是裸 `<button>` 页签；`Practice.vue` 全手写 CSS，颜色硬编码 `#1a1a2e`/`#16213e`/`#0f3460`；装了 Tailwind 但**前端代码里一次没用** | PU-7 ~ PU-10 |
| 5 | 题目硬编码 | `Practice.vue:28` 写死 `'A + B Problem'`，实为 A+B；`Problem` 表只有 `id/title/knowledge_tags` | PU-11 ~ PU-14 |
| 6 | 只支持 C++ | `compiler.py` 的 `compile_cpp` 硬编码 `settings.cpp_compiler_path` + `-std={cpp_std}`；`compile_and_run` 里 `src_path = tmp / "main.cpp"` 也是写死的 | PU-15 ~ PU-17 |
| 7 | 提交不入库（隐性阻塞） | `submissions` 表无写入接口，而 `MistakeBookEntry` 要求 `submission_id` 且该提交必须已存在 → 错题本正常使用路径断裂 | PU-13 |

> **本阶段不做什么**：不接 LLM、不动 `agent/` 目录、不引入 vue-router 之外的
> 新依赖（除非切片明确列出）、不做打包（S10）。

---

## 一、架构决策（动手前必须读完）

### 决策 1：启动方式——Electron 主进程直接 spawn，废弃启动脚本拉 sidecar

**现状问题**：`start.ps1`/`start.bat` 各自负责「拉起 sidecar + 拉起 Electron」，
与 `desktop/src/main/sidecar.ts` 里的 spawn 逻辑**重复且行为不一致**：

- 脚本固定 `--port 8000`；`sidecar.ts` 用 `getAvailablePort()` 随机端口
- 脚本用 `--reload`（开发热重载）；`sidecar.ts` 不带
- 脚本的 sidecar 日志走 `Tee-Object` 到文件；`sidecar.ts` 走 `process.stdout`
- 脚本用 `Start-Process` 起一个**可见窗口**的 PowerShell → 黑框来源

**决策**：**Electron 成为唯一启动者**。

```
用户双击（快捷方式/终端 npm run dev）
        ↓
Electron main 进程启动
        ↓
显示 Splash 窗口（无边框、居中、带动画）    ← PU-2
        ↓
主进程 spawn python -m uvicorn（windowsHide: true）  ← PU-1
        ↓
轮询 /health 直至 200
        ↓
关闭 Splash → 显示主窗口（带淡入）           ← PU-2
```

**启动脚本降级为「便捷包装」**：`start.bat` / `start.ps1` 只保留
「设置 PATH + `cd desktop && npm run dev`」这两件事，**不再自己拉 sidecar**。
黑框问题由此消失（Electron 的 spawn 带 `windowsHide`）。

> **为什么不用 `.vbs` 转无窗口**：那是绕过问题而非解决问题，且调试困难。
> 主进程 spawn 是 Electron 的标准做法，同时解决了「双启动路径不一致」这个
> 已在 MEMORY.md 中记录的已知薄弱点。

### 决策 2：网页端——共享 renderer，抽出「运行时适配层」

**现状问题**：`api/http.ts` 直接调 `window.algoforge.sidecar.getInfo()`。
这是 Electron preload 注入的。浏览器里 `window.algoforge` 是 `undefined`，
调 `.getInfo()` 直接 `TypeError`。

**决策**：引入 **runtime adapter**——renderer 不再直接触碰 `window.algoforge`，
而是调一个抽象接口，由构建目标决定注入哪个实现。

```
login/renderer 代码
        ↓ 调用
import { runtime } from '@/runtime'      ← 唯一入口
        ↓ 内部按构建目标选择实现
   ┌────────────────┬──────────────────────┐
   │ electronRuntime │  webRuntime          │
   │ (读 window.     │  (从 import.meta.env │
   │  algoforge)     │   或 /config.json)   │
   └────────────────┴──────────────────────┘
```

`runtime` 需要暴露的最小接口：

```ts
export interface Runtime {
  /** 侧车的 base URL + token */
  getSidecarInfo(): Promise<{ url: string; token: string } | null>
  /** 该运行环境的能力声明，UI 据此隐藏/禁用功能 */
  capabilities: {
    /** 能否直接读写本地文件（Electron 可，Web 不可） */
    localFileSystem: boolean
    /** 能否随应用退出自动结束 sidecar（Web 由服务端管理） */
    managesSidecarProcess: boolean
  }
}
```

**Web 端 sidecar 的地址来源**（按优先级）：
1. `import.meta.env.VITE_SIDECAR_URL`（`.env.web` 注入）
2. `window.__ALGOFORGE_CONFIG__`（服务端在 `index.html` 内联注入，供部署时改）
3. 同源兜底：`window.location.origin`（sidecar 与静态资源同域反代时成立）

> **CORS 已就绪**：`sidecar/app/main.py` 已从 `["*"]` 改为
> `settings.resolved_cors_origins`（可配）。Web 端部署时把前端源写进
> `CORS_ALLOW_ORIGINS` 即可，无需改后端代码。

**Web 端的能力降级**（必须显式处理，不能假装没差别）：

| 功能 | Electron | Web | 处理方式 |
|---|---|---|---|
| 判题 / 错题本 / 画像 / 复习 | ✅ | ✅ | 无差异 |
| sidecar 生命周期 | 主进程管理 | 服务端管理 | `capabilities.managesSidecarProcess` |
| 本地文件打开/保存 | ✅ | ❌ | 按 capability 隐藏按钮 |
| 无边框自定义标题栏 | ✅ | ❌ | 按 capability 换普通顶栏 |
| Monaco 编辑器 | ✅ 本地 | ✅ CDN/打包 | 无差异 |

### 决策 3：路由——引入 vue-router

**现状**：`App.vue` 用 `ref<Tab>` 手写页签。注释理由是「单用户桌面应用，页面少」。

**为什么现在必须换**：
1. Web 端需要 **URL**——刷新后要回到当前页，能分享 `/#/practice` 链接
2. 题库上线后页面会变多（题目列表 / 题目详情 / 提交记录），手写 `v-if` 链会失控
3. 需要路由守卫做「sidecar 未就绪 → 显示加载态」的统一处理

**决策**：引入 `vue-router@4`，用 **hash 模式**（`createWebHashHistory`）。
hash 模式对 Electron 的 `file://` 与静态托管都友好，无需服务端 rewrite 规则。

> 这是**本阶段唯一新增的运行时依赖**。若你不同意，替代方案是保留手写页签
> 并接受 Web 端无 URL——但 PU-11 的题库页面数量会让这个选择很痛苦。

### 决策 4：多语言——语言描述符（Language Descriptor），不用类继承

**现状问题**：`compiler.py` 把 g++ 的三件事硬编码进去了：
1. 编译器可执行文件路径（`settings.cpp_compiler_path`）
2. 编译参数（`-std=c++17`、`-o <out>`）
3. 源文件扩展名（`main.cpp`，且在 `compile_and_run` 里也是写死的）

Python / Java 的差异：

| 语言 | 编译 | 运行 | 源文件名要求 |
|---|---|---|---|
| C++ | `g++ main.cpp -o main.exe -std=c++17` | `main.exe` | 任意 |
| Python | 无（或 `python -m py_compile` 语法检查） | `python main.py` | 任意 |
| Java | `javac Main.java` | `java -cp . Main` | **必须 `Main.java`（public class 名须与文件名一致）** |

Java 的「文件名必须匹配 public class 名」是**结构性差异**，不能用
「同一套流程换个命令行」糊过去——所以需要描述符里有 `source_filename` 字段。

**决策**：定义 `LanguageSpec` dataclass（不是继承体系——三种语言的差异用数据
描述就够了，上类继承属于过度设计，违反 VIBECODING.md「不顺手优化」精神）：

```python
# sidecar/app/sandbox/languages.py
@dataclass(frozen=True)
class LanguageSpec:
    id: str                      # "cpp" | "python" | "java"
    display_name: str            # "C++" / "Python" / "Java"
    source_filename: str         # "main.cpp" / "main.py" / "Main.java"
    monaco_language: str         # Monaco 的 language id
    default_template: str        # 新建文件时的初始代码
    needs_compile: bool          # Python 为 False
    compile_cmd: Callable[[Path, Path], list[str]] | None
    run_cmd: Callable[[Path], list[str]]
    version_cmd: list[str]       # 探测编译器版本，用于环境自检
```

关键点：`compile_cmd` / `run_cmd` 是**根据路径生成 argv 的函数**，而不是
固定字符串列表——因为 Java 的 `java -cp <dir> Main` 需要从路径推导目录。

**Python 的「编译」语义**：Python 无编译期，但需要**语法检查**以对齐 CE
体验。用 `python -m py_compile <file>` 作为编译步骤，语法错误即 CE。
（注意：`py_compile` 会在源文件旁生成 `__pycache__`，需在临时目录内，随
`TemporaryDirectory` 一起清理。）

**Java 的内存限制**：`run_executable` 用 psutil 轮询 RSS——对 `java` 进程
**不准**，因为 JVM 启动即占几十 MB，且实际堆在子进程里。建议 Java 运行时
额外传 `-Xmx{mem_limit_mb}m`，并**放宽 MLE 判定的默认内存上限**（见 PU-16）。

---

## 二、切片清单

> 编号接续：P1-23 之后是 PU-0。每个切片给出**产出 / 内容 / 验证 / 前置**。
> 「Agent 预留」标记表示该切片产出的接口是 Phase 2 你手写 Agent 时的输入。

---

### 第 A 组：启动体验（PU-0 ~ PU-3）

#### PU-0：Tailwind 生效性验证 + 设计令牌（Design Tokens）

- **产出**：`desktop/tailwind.config.js`（补全）、`desktop/src/renderer/src/assets/tokens.css`
- **内容**：
  1. **先验证 Tailwind 是否真的接上了**。`package.json` 里**没有 tailwindcss
     依赖**，但 `AGENTS.md` 要求用 Tailwind——需确认是走 `@tailwindcss/vite`
     插件还是根本没装。若未装，本切片先补上（v4 用 `@tailwindcss/vite` 插件，
     v3 用 `postcss`）。
  2. 定义语义化 CSS 变量（**这是后续所有页面改版的基础**）：
     ```css
     :root {
       --af-bg-canvas: #0b1020;      /* 最底层背景 */
       --af-bg-surface: #141b2d;     /* 卡片/面板 */
       --af-bg-elevated: #1c2438;    /* 悬浮/弹层 */
       --af-border: #263149;
       --af-text-primary: #e8ecf5;
       --af-text-secondary: #9aa7bd;
       --af-text-muted: #66738c;
       --af-accent: #4f8cff;         /* 主操作色 */
       --af-accent-hover: #6ba1ff;
       --af-success: #22c55e;
       --af-warning: #f59e0b;
       --af-danger: #ef4444;
       /* 判题状态色——与后端 JudgeStatus 一一对应 */
       --af-verdict-ac: #22c55e;
       --af-verdict-wa: #ef4444;
       --af-verdict-tle: #f59e0b;
       --af-verdict-mle: #a855f7;
       --af-verdict-ce: #64748b;
       --af-verdict-re: #e11d48;
     }
     ```
  3. 这些变量既被 Tailwind 的 `theme.extend.colors` 引用，也被 Monaco 的
     自定义主题引用（PU-10 用）——**单一真源**，避免颜色散落。
- **验证**：新建一个临时组件，用 `class="bg-surface text-primary"` 能看到
  样式生效；`tokens.css` 被 `main.ts` 引入且无控制台报错。
- **前置**：无
- **为什么先做**：AGENTS.md 写明「能用 Tailwind 就不用 Sass/手写 CSS」，
  而现状是**全项目一处 Tailwind 都没用**。先把这个基建补齐，后续改版才是
  「应用设计系统」而不是「手写另一套 CSS」。

> ⚠️ **本切片必须诚实报告**：如果发现 Tailwind 其实没装、`AGENTS.md` 的
> 约束从未落实，这本身就是一个需要你知晓的事实。不要默默补上后假装一直在用。

#### PU-1：主进程 spawn 去窗口化 + 优雅关闭

- **产出**：修改 `desktop/src/main/sidecar.ts`、修改 `desktop/src/main/index.ts`
- **内容**：
  1. `spawn` 选项加 `windowsHide: true`（Windows 下不弹控制台）。
     确认 `stdio: ['ignore','pipe','pipe']` 保持——日志仍要能进主进程终端。
  2. **关闭时杀进程树**。现状 `sidecarProcess.kill()` 在 Windows 上不杀
     uvicorn 派生的工作进程。改为：
     ```ts
     // Windows: taskkill /PID <pid> /T /F   （/T 连子进程一起杀）
     // POSIX:   process.kill(-pid)          （需 spawn 时 detached: true 建进程组）
     ```
  3. **`stopSidecar()` 改为 async 并等待退出**，`window-all-closed` 里
     `await` 它（现状是 fire-and-forget，Electron 可能在 sidecar 未退出时
     就已 `app.quit()`，导致孤儿进程——这正是 MEMORY.md 记录的已知薄弱点）。
  4. 加**兜底清理**：`app.on('before-quit')` 与 `process.on('exit')` 双保险。
- **验证**：
  - Windows 任务管理器确认：应用启动时**无新增 cmd/powershell 窗口**
  - 关闭应用后 `tasklist | findstr python` 无残留 uvicorn 进程
  - 主进程终端仍能看到 `[sidecar]` 日志前缀
- **前置**：无

#### PU-2：Splash 启动窗口 + 就绪过渡动画

- **产出**：`desktop/src/main/splash.ts`（主进程侧窗口管理）、
  `desktop/src/renderer/splash.html`、`desktop/src/renderer/src/assets/splash.css`
- **内容**：
  1. **Splash 窗口**：无边框（`frame: false`）、固定尺寸（如 420×280）、
     居中、`transparent: true` 以支持圆角、`alwaysOnTop: true`、
     `resizable: false`、不可聚焦（`focusable: false`）避免抢焦点。
  2. **动画内容**（纯 CSS，不用 JS 动画库）：
     - Logo（可用已有的 `electron.svg` 改造，或先用文字「AlgoForge」+ 渐变）
     - 三段式进度文案，由主进程通过 IPC 推送：
       `初始化运行时…` → `启动分析引擎…` → `准备就绪`
     - 进度条：**不确定态**（indeterminate，来回扫动）而非百分比——
       因为 sidecar 冷启动时间不稳定，假百分比会跳变显得廉价。
         若想要确定性进度，就把它做成**阶段式**（3 个阶段各占 33%），
       而不是伪造时间百分比。
  3. **过渡**：主窗口 `ready-to-show` 后——
     - Splash 淡出（`opacity: 0`，200ms）
     - 主窗口淡入 + 轻微缩放（`scale(0.98) → scale(1)`，250ms，ease-out）
     - Splash 在动画结束后才 `destroy()`，避免闪现空白
  4. **冷启动加速**：`main/index.ts` 现状是「`await startSidecar()` 完成
     后才 `createWindow()`」——这是**串行**的，用户盯着 Splash 干等。
     改为**并行**：立刻显示 Splash，同时 `startSidecar()` 与
     `createWindow()`（窗口 `show: false`）并行，健康检查通过后再显示主窗口。
- **验证**：
  - 启动时看到动画，无白屏闪烁
  - 主进程日志与界面文案阶段同步（改 sidecar 启动耗时用 `sleep` 模拟，
    确认文案确实在推进而非跳到最后一步）
  - 启动期间 Splash 不能被拖动/点击出异常
- **前置**：PU-1
- **可选加分**：`app.setUserModelId` 现状是 `'com.electron'`（模板默认值），
  顺手改成 `'com.algoforge.desktop'`——这会影响任务栏图标归组。属本切片范围。

#### PU-3：启动脚本降级 + 环境自检

- **产出**：重写 `start.ps1`、`start.bat`；新增 `sidecar/app/api/env_check.py`
  与 `GET /api/v1/env-check`
- **内容**：
  1. **脚本只做两件事**：设 PATH、`cd desktop && npm run dev`。
     **不再拉 sidecar**（PU-1 后由主进程负责）。删掉 `$SidecarPort`、
     `Start-Process`、`Tee-Object`、`Start-Sleep` 全部逻辑。
  2. **根目录 `.env.example`**：现状 `.env.example` 在 `sidecar/` 下。
     加一个根目录的说明性 README 段落，或把启动所需变量集中文档化。
  3. **环境自检 API**（这是为 S2 及后续 Agent 工作预留的**预准备**）：
     ```python
     GET /api/v1/env-check
     → {
         "languages": [
           {"id": "cpp", "available": true,  "version": "g++ (UCRT64) 13.2.0", "path": "D:\\...\\g++.exe"},
           {"id": "python", "available": true, "version": "Python 3.13.12", "path": "..."},
           {"id": "java", "available": false, "version": null,  "path": null}
         ],
         "database": {"ok": true, "dialect": "postgresql"},
         "sandbox_tmp_writable": true
       }
     ```
     实现方式：对每个语言跑 `version_cmd`（如 `g++ --version`）并解析首行；
     Java 缺失时 `available: false` **而不是抛错**。
  4. **前端**：启动时调一次 `env-check`，若有语言不可用，在对应语言选项上
     显示禁用态 + 提示（PU-8 用）。
- **验证**：
  - `start.ps1` 运行后**只有一个**窗口（Electron），无 cmd/PowerShell 黑框
  - `curl` `env-check` 返回三语言状态，故意把 Java 从 PATH 移除能看到
    `available: false` 而不是 500
  - **本机注意**：Java 大概率未安装。本切片只做「探测 + 报告」，
    **不负责安装 Java**——安装 Java 是环境准备，需你确认后另行处理
- **前置**：PU-1
- **Agent 预留**：`env-check` 的输出结构会被 Phase 2 的 Agent 复用
  （Agent 判断「该语言环境是否可跑」时不必重复探测）

---

### 第 B 组：网页端（PU-4 ~ PU-6）

#### PU-4：运行时适配层（Runtime Adapter）

- **产出**：`desktop/src/renderer/src/runtime/types.ts`、
  `runtime/electron.ts`、`runtime/web.ts`、`runtime/index.ts`
- **内容**：按「决策 2」实现 `Runtime` 接口。`runtime/index.ts` 做选择：
  ```ts
  // 构建期由 Vite 的 define/alias 决定注入哪个实现，
  // 而不是运行期 if (window.algoforge) —— 后者会把两套代码都打进产物。
  export const runtime: Runtime = __IS_ELECTRON__ ? electronRuntime : webRuntime
  ```
  使用 `import.meta.env.VITE_TARGET`（值 `electron` | `web`）或 Vite 的
  `resolve.alias` 在构建期替换。**关键**：Web 构建产物里不应包含
  `window.algoforge` 相关代码路径。
- **内容（续）**：改造 `desktop/src/renderer/src/api/http.ts`：
  `initHttpClient()` 改为 `await runtime.getSidecarInfo()`，
  **不再直接访问 `window.algoforge`**。
- **验证**：
  - Electron 构建：`npm run dev` 一切照旧
  - 用 `VITE_TARGET=web` 构建后 `grep -r "algoforge.sidecar" dist/`
    应无匹配（证明没有把 Electron 路径打进 Web 包）
- **前置**：无

#### PU-5：Web 构建目标与开发服务器

- **产出**：`desktop/electron.vite.config.ts`（新增 web target）、
  `desktop/vite.config.web.ts`（独立 web 构建配置）、
  `desktop/package.json` 新增 script、`desktop/.env.web.example`
- **内容**：
  1. **独立 web 构建**：`npm run build:web` → 输出到 `dist-web/`。
     复用同一份 `renderer` 源码，通过 `VITE_TARGET=web` 切换适配层。
  2. **开发态**：`npm run dev:web` 用 `vite --config vite.config.web.ts`，
     默认 5173 端口。此时**需要 sidecar 已在跑**——开发时可先用
     `sidecar/.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000`
     手动拉一个（记得 `SIDECAR_TOKEN` 要与前端 `VITE_SIDECAR_TOKEN` 一致）。
  3. **`desktop/.env.web.example`**：
     ```env
     VITE_TARGET=web
     VITE_SIDECAR_URL=http://127.0.0.1:8000
     VITE_SIDECAR_TOKEN=
     ```
     > ⚠️ **安全提示**：把 Token 打进前端产物是**可被看到**的。Web 部署若
     > 面向公网，必须改为「服务端反代 + 会话鉴权」，而不是前端塞 Token。
     > 本切片默认是**局域网/本机 Web 预览**场景，需要在文档里写明这个边界。
  4. **CORS**：在 `sidecar/.env.example` 的 `CORS_ALLOW_ORIGINS` 注释里
     补上 web 开发态示例（`http://localhost:5173`）。
- **验证**：
  - `npm run build:web` 产物可在浏览器打开，`/health` 显示连接成功
  - `npm run dev:web` 下改代码有 HMR
  - 用 `curl -H "Origin: http://localhost:5173" -i <sidecar>/health`
    确认 CORS 头正确
- **前置**：PU-4

#### PU-6：能力降级 UI + 响应式布局基线

- **产出**：修改 `desktop/src/renderer/src/App.vue` 及其布局壳、
  新增 `components/TitleBar.vue`、`components/AppSidebar.vue`
- **内容**：
  1. **标题栏适配**：
     - Electron：保留系统原生标题栏（本阶段**不做**无边框自定义标题栏——
       那会引入拖拽区/最小化/最大化/关闭的全套 IPC，收益低于成本）
     - Web：显示一个简单的品牌顶栏（左侧 Logo + 标题 + 右侧环境标识）
  2. **按 `runtime.capabilities` 隐藏不可用功能**：
     例如未来的「导出到本地文件」按钮，Web 端禁用 + tooltip 说明原因。
  3. **响应式断点**（浏览器窗口尺寸不受控，必须处理）：
     - `≥1280px`：侧边栏展开（图标 + 文字）
     - `1024–1280px`：侧边栏折叠（仅图标）
     - `<1024px`：侧边栏收起为抽屉（汉堡菜单）
     - **Practice 页的双栏**（题目 360px + 编辑器）在 `<900px` 时改为
       上下堆叠（题目在上，编辑器在下），否则 Monaco 会被压成一条缝
  4. 现状 `Practice.vue` 的 `.problem-panel { width: 360px; flex-shrink: 0 }`
     是硬编码宽度——改为 CSS 变量 / Tailwind 类。
- **验证**：
  - Electron 窗口拉到最小尺寸，Practice 页不出现横向滚动条
  - 浏览器窗口从 1600px 拖到 800px，能看到三档断点切换且无布局崩坏
- **前置**：PU-4、PU-5、PU-0

---

### 第 C 组：视觉改版（PU-7 ~ PU-10）

> **改版原则**：状态色**必须**与后端 `JudgeStatus` 语义绑定，不能各页面
> 自己定义。AC 用 `--af-verdict-ac`，不允许在组件里写 `#22c55e`。

#### PU-7：应用壳层——侧边导航 + 顶栏 + 内容区

- **产出**：重写 `App.vue`、`components/AppSidebar.vue`、`components/AppTopbar.vue`
- **内容**：
  1. 引入 vue-router（决策 3），`App.vue` 只剩 `<RouterView>` + 壳层。
  2. **左侧边栏**：Logo + 导航项（练习 / 题库 / 错题本 / 画像 / 复习）+ 底部
     环境状态指示（由 PU-3 的 `env-check` 驱动，绿点=就绪）。
     当前项高亮用左侧 3px 竖条 + 背景微亮，而非整块填充色。
  3. **顶栏**：面包屑（如 `题库 / 二分查找 / 提交`）+ 右侧操作位。
  4. **内容区**：统一 `max-width` 与内边距，滚动容器独立（现状 `App.vue`
     的 `.tab-content { overflow: hidden }` 会导致长页面无法滚动——
     Practice 页靠内部 `overflow-y: auto` 自救，属脆弱的隐式约定）。
  5. **加载/错误态**：sidecar 未就绪时，内容区显示骨架屏而非空白。
- **验证**：
  - 五个导航项都能切换，URL 随之变化（hash 路由）
  - 浏览器刷新后停留在当前页
  - sidecar 故意不启动时，界面显示「连接失败 + 重试按钮」而非白屏
- **前置**：PU-0、PU-4、PU-6

#### PU-8：练习页改版（Practice）

- **产出**：重写 `views/Practice.vue`、重构 `components/OutputPanel.vue`
- **内容**：
  1. **三栏布局**（桌面）：左侧题目描述（可折叠）/ 中间 Monaco / 右侧或底部
     输出。或维持两栏但**用 Tailwind 重写**，去掉全部硬编码色值。
  2. **工具栏**：语言选择器（PU-15 后可用，暂显 C++）、
     「运行」与「提交」两个按钮语义分离——
     - **运行**：跑当前测试用例，不判分不入库（现状就是这个行为）
     - **提交**：跑完整用例集，判分并写 `submissions`（PU-13 后可用）
     现状只有一个「运行判题」按钮，把两件事混在一起，需要在本次拆开。
  3. **状态徽章**：AC/WA/TLE/MLE/CE/RE 用统一的 `VerdictBadge.vue` 组件，
     颜色取 PU-0 的 verdict 变量。**这个组件在错题本、提交记录里都要复用**。
  4. **加载与进度**：现状 `isRunning` 只切按钮文案。改为在输出面板顶部
     显示正在跑第几个用例（WS 的 `case_start` 事件已带 `index`）。
  5. **键盘快捷键**：`Ctrl/Cmd + Enter` 运行，`Ctrl/Cmd + Shift + Enter` 提交。
     这是 IDE 惯例，用户预期强烈。
- **验证**：
  - 跑一次 AC、一次 WA，徽章颜色正确
  - 快捷键可用
  - 浏览器与 Electron 下布局都正常
- **前置**：PU-6、PU-7

#### PU-9：错题本 / 画像 / 复习页统一改版

- **产出**：重写 `views/MistakesView.vue`、`views/ProfileView.vue`、
  `views/PlanView.vue`、`components/RadarChart.vue`
- **内容**：
  1. 三页统一视觉语言：卡片、表头、空状态、加载态都由 PU-0 令牌驱动。
  2. **空状态要说话**：现状三页数据为空时大概率是空白。改为
     「还没有错题记录，去练习页提交一次试试 →」+ 跳转按钮。
  3. **错题本**：列表 + 知识点筛选（现状已有）+ 预留错因筛选位
     （PU-14 与 Phase 2 的 P2-21 用，本阶段**显示为禁用态**并标注
     「AI 接入后可用」——诚实告知而非隐藏）。
  4. **画像**：雷达图主题适配 PU-0 令牌；补一个「数据不足」态
     （提交数 < 3 时雷达图无意义）。
  5. **复习**：SM-2 的「今日待复习」列表 + 质量评分交互（1-5 分），
     评分后立即更新下次复习时间（现状后端已支持，确认前端接对）。
- **验证**：三页在有数据/无数据两种状态下都不崩、不空白
- **前置**：PU-7

#### PU-10：Monaco 主题定制 + 语言注册

- **产出**：`desktop/src/renderer/src/monaco-theme.ts`、修改 `monaco.ts`、
  `components/CodeEditor.vue`
- **内容**：
  1. 定义 Monaco 自定义主题 `algoforge-dark`，用 `defineTheme` 把 PU-0
     的令牌映射进去（背景 `--af-bg-surface`、关键字/字符串/注释各自着色）。
     **注意**：Monaco 的 `defineTheme` 需要**十六进制字面值**，不认 CSS
     变量——所以在 `monaco-theme.ts` 里要从 `getComputedStyle` 读一次变量
     再传入，或维护一份导出的常量并让 `tokens.css` 与之同步。
  2. **默认语言改为从题目/用户选择读取**，不再是硬编码 `language="cpp"`
     （现状 `Practice.vue:155`）。
  3. `CodeEditor.vue` 的 props 增加 `language: LanguageId`，
     内部维护「切换语言时是否保留代码」的策略：
     切换语言若当前代码被修改过，弹确认框（避免误删用户代码）。
- **验证**：
  - 编辑器主题与应用整体视觉一致
  - 切换 C++/Python/Java 时 Monaco 语法高亮随之改变
- **前置**：PU-0、PU-8

---

### 第 D 组：题库系统（PU-11 ~ PU-14）

> **这一组是 Phase 2 的关键预准备**：Agent 的错因分析质量高度依赖
> 「题目上下文 + 真实测试用例」。题库不只是 UI 需求，它是 Agent 的输入源。

#### PU-11：Problem 模型扩展 + Alembic 迁移

- **产出**：修改 `sidecar/app/models/problem.py`、新增迁移文件、
  `sidecar/app/models/test_case.py`（新表）
- **内容**：
  1. **`Problem` 扩字段**（`problem.py` 现有注释已写明这些是 P1-8 预留的）：
     ```python
     description: Mapped[str]         # Markdown 正文
     input_format: Mapped[str | None]
     output_format: Mapped[str | None]
     samples: Mapped[list]            # JSON: [{"input":..., "output":...}] 展示用
     time_limit: Mapped[float]        # 秒
     memory_limit: Mapped[int]        # MB
     difficulty: Mapped[str | None]   # "easy" | "medium" | "hard"
     source: Mapped[str | None]       # 出处，如 "POJ 1000"
     is_active: Mapped[bool]          # 软删除/下架，题库增删用
     ```
     > `knowledge_tags` 已存在（JSON），保留。
  2. **新增 `TestCase` 表**（**关键决策**）：现状测试用例是前端硬编码 +
     WS 请求里随代码一起传的。题库化后必须**由后端持有**：
     ```python
     class TestCase(Base):
         id, problem_id (FK, index), ordinal (int),
         input (Text), expected_output (Text),
         is_sample (bool)   # True=展示给用户，False=隐藏判题用例
     ```
     **为什么必须有 `is_sample`**：OJ 的核心是**隐藏用例**。若所有用例都
     展示，用户可以硬编码答案。现状 `Practice.vue` 把 `testCases` 直接写在
     前端，等于把全部用例公开——这是**判题系统的根本性缺陷**，不只是
     「硬编码不好看」。
  3. 迁移用 Alembic（`alembic revision --autogenerate`），**不手写 ALTER TABLE**。
  4. **保留 `compile_and_run` 的向后兼容**：`run_code.py` 的
     `POST /api/v1/run-code` 接收裸 `code`，不依赖题目——保持不变。
- **验证**：
  - `alembic upgrade head` 成功，`alembic downgrade -1` 也能回滚
  - pytest 插入 Problem + 3 个 TestCase 并查询
  - **迁移必须在真实 PostgreSQL 上验证**（`DATABASE_URL` 指向 PG）——
    不能只在 SQLite 测试里通过就宣称完成（JSON 字段类型在两库行为不同）
- **前置**：无

#### PU-12：题库 API

- **产出**：`sidecar/app/api/problems.py`、在 `main.py` 注册
- **内容**：
  ```
  GET  /api/v1/problems                 → 列表（分页、按知识点/难度筛选）
  GET  /api/v1/problems/{id}            → 详情（含 samples，不含隐藏用例）
  GET  /api/v1/problems/{id}/test-cases → 判题用完整用例（供 WS 流程使用）
                                        ⚠️ 见下方安全说明
  POST /api/v1/problems                 → 创建（题库录入用，本阶段可先留）
  ```
  **安全说明**：`GET /problems/{id}/test-cases` 会把隐藏用例暴露给前端。
  在**单用户本地应用**场景下这是可接受的（用户就是题目作者/自己练题），
  但**必须在文档和代码注释里写明这个边界**。若将来做 Web 端多用户，
  判题必须改为「后端持有用例，前端只提交代码」——即 WS 请求里不再传
  `test_cases`，只传 `problem_id`。
  > **建议**：**现在就按多用户安全的方式设计**——WS 请求支持
  > `problem_id`（后端查用例）与 `test_cases`（临时自测）二选一。
  > 这样 Web 端上线时不需要重构判题链路。
- **验证**：curl 走一遍列表/详情；WS 用 `problem_id` 模式判题成功
- **前置**：PU-11

#### PU-13：提交记录写入 + 判题链路打通（**修复断层 7**）

- **产出**：修改 `sidecar/app/models/submission.py`、新增
  `sidecar/app/api/submissions.py`、修改 `sidecar/app/api/ws.py` 与
  `sidecar/app/api/judge.py`
- **内容**：
  1. **`Submission` 扩字段**（注释已写明是 P1-9 预留）：
     `code`、`language`、`stdout`、`stderr`、`exec_time`、`memory_used`
     以及 `problem_id` 可空（支持无题目的自由运行）。
  2. **判题完成即落库**：这需要一个**写 Session**（现状判题链路完全不碰
     数据库）。注意 `judge()` 是同步函数、跑在 `asyncio.to_thread` 里，
     落库要用**同步 Session**（`db.py` 的 `_resolve_sync_url()` 已支持
     psycopg 同步驱动）——不要在 worker thread 里用 async Session。
  3. **`GET /api/v1/submissions`** 列表 + `GET /api/v1/submissions/{id}` 详情。
  4. **打通错题本数据来源**：现状 `MistakeBookEntry` 要求 `submission_id`
     且该提交必须已存在，但没有任何接口写 `submissions` —— 这就是断层 7。
     本切片完成后，错题本的正常路径才成立。
  5. **是否自动归档错题**：README 宣称「错题自动归档」，但代码层面不成立。
     本切片做一个**明确选择**并写进文档：
     - 方案 A：WA 时自动创建 `MistakeBookEntry`（`error_type` 留 null，
       Phase 2 回填）——**推荐**，符合 README 承诺
     - 方案 B：仅落 `submissions`，错题由用户手动加入
     > 推荐 A。若选 A，注意**幂等**：同一份 WA 代码重复提交不应产生重复错题。
- **验证**：
  - 提交一次 AC、一次 WA，`GET /submissions` 能看到两条记录
  - WA 后 `GET /api/v1/mistakes` 能看到对应错题（若选方案 A）
  - **pytest 覆盖**：这属于并发/线程边界，写一个「worker thread 落库」
    的集成测试，避免在主线程测试里掩盖线程问题
- **前置**：PU-11、PU-12

#### PU-14：题库语料 + 题目列表 UI

- **产出**：`corpus/problems/*.yaml`（20 道）、`tools/load_problems.py`、
  `desktop/src/renderer/src/views/ProblemListView.vue`
- **内容**：
  1. **20 道经典题**，覆盖不同知识点与难度（建议分布）：
     | 分类 | 数量 | 示例 |
     |---|---|---|
     | 入门/模拟 | 4 | A+B、闰年判断、水仙花数、简单排序 |
     | 循环/枚举 | 3 | 素数筛、最大公约数、鸡兔同笼 |
     | 数组/字符串 | 3 | 数组逆序、回文串、统计字符 |
     | 二分 | 2 | 二分查找、方程求根 |
     | 贪心 | 2 | 区间调度、找零钱 |
     | 动态规划 | 3 | 爬楼梯、最长上升子序列、01 背包 |
     | 图论 | 2 | BFS 最短路、连通块计数 |
     | 递归/分治 | 1 | 汉诺塔 |
  2. **每题必须包含**：
     - `description`（Markdown）
     - 至少 **2 个 sample 用例**（展示用，需人工核对输出正确）
     - 至少 **3 个隐藏用例**（判题用，含**边界用例**）
     - `correct_reference`（**参考解代码**，C++）——**这是硬要求**，见下
     - `knowledge_tags`、`difficulty`、`source`
  3. **`correct_reference` 为什么是硬要求**（呼应 `corpus/eval_cases/`
     的既有约定）：Phase 2 的 Agent 错因分析评估集（`corpus/eval_cases/`）
     已有「每道用例必须有 `correct_code` 且必须 AC」的规范。
     题库若也带参考解，Phase 2 可以**直接复用题库生成错因评估集**，
     不必另起炉灶。同时 `load_problems.py` 可**自动校验**：
     跑参考解，断言所有用例 AC——**用参考解 AC 来证明这道题的用例是自洽的**。
  4. **`tools/load_problems.py`**：读 `corpus/problems/*.yaml`，
     编译运行参考解校验用例 → 全部通过才写入数据库。
     **校验不过则拒绝入库并报错**，不污染题库。
  5. **前端 `ProblemListView.vue`**：
     - 列表：标题、难度徽章、知识点标签、通过状态（从 submissions 聚合）
     - 筛选：知识点 / 难度 / 是否通过
     - 点击进入 Practice 页并加载该题（路由参数 `problemId`）
  6. **Practice 页接入**：题目来源从硬编码改为路由参数 →
     `GET /problems/{id}` + `/test-cases`。无 `problemId` 时回退到
     「自由运行」模式（现状行为，保留作为草稿箱）。
- **验证**：
  - `python tools/load_problems.py` 成功导入 20 道，且**每道都通过
    参考解 AC 校验**（这是本切片最强的验证）
  - 前端能切换题目，切换后 Monaco 加载该题的语言模板，判题正常工作
  - 故意在某个 yaml 里写错 sample 输出 → 脚本报错并拒绝入库
- **前置**：PU-11、PU-12、PU-13
- **Agent 预留**：`corpus/problems/*.yaml` 的 `correct_reference` +
  隐藏用例，是 Phase 2 生成「带标注错因的评估用例」的原料。
  在 yaml 里预留一个 `eval_notes` 字段（可空），描述「常见错误解法及其
  错因标签」——Phase 2 可直接消费。

---

### 第 E 组：多语言适配（PU-15 ~ PU-17）

#### PU-15：语言描述符层

- **产出**：`sidecar/app/sandbox/languages.py`
- **内容**：按「决策 4」实现 `LanguageSpec` + 三个具体实例
  （`CPP_SPEC`、`PYTHON_SPEC`、`JAVA_SPEC`）+ `get_language(id)` 注册表。
  `config.py` 补字段：`python_interpreter_path`、`java_compiler_path`、
  `java_runtime_path`、`java_max_heap_mb`（可选）。
- **验证**：pytest 断言三个 spec 的 `source_filename` / 命令生成函数正确；
  未知语言 id 抛明确错误（而非 `KeyError`）
- **前置**：无
- **注意**：本切片**只定义描述符，不改 `compiler.py`**——先立接口后填实现
  （VIBECODING.md 第 5 条）。

#### PU-16：编译器/运行器多语言化

- **产出**：重写 `sidecar/app/sandbox/compiler.py`、修改 `runner.py`、
  修改 `judge.py`、修改 `api/ws.py` 与 `api/run_code.py` 的请求模型
- **内容**：
  1. `compile_cpp(source_path)` → `compile_source(source_path, language)`，
     内部查 `LanguageSpec`。**保留 `compile_cpp` 作为薄封装**，避免
     打断 Phase 1 的既有测试（`tests/test_compiler.py` 等）。
  2. `compile_and_run(code)` → `compile_and_run(code, language="cpp")`
     —— 默认参数保证向后兼容。
  3. **`source_filename` 必须来自 spec**，不能写死 `main.cpp`。
     Java 的 `Main.java` 依赖此机制。
  4. **`stdin` 与编码**：`runner.py` 现状未处理编码。Python 输出含中文时
     在 Windows 下可能以 GBK 编码 —— 需要统一 `encoding="utf-8",
     errors="replace"`，并在 Python 运行时设 `PYTHONIOENCODING=utf-8`
     （通过 `_build_env` 注入）。
  5. **测试用例执行顺序调整**：`compile_and_run` 现状用
     `tempfile.TemporaryDirectory` —— **注意本机约束**：用户名含中文，
     MinGW `ld.exe` 无法在非 ASCII 路径写输出。`TemporaryDirectory`
     默认在 `%TEMP%`（路径含中文用户名）下！需要在 spec 或
     `compiler.py` 里指向 `C:\Windows\Temp`（`judge.py` 与
     `tests/conftest.py` 已有此 workaround，**必须沿用，新代码别忘**）。
  6. `ws.py` 的请求模型加 `language` 字段（默认 `"cpp"`），
     `run_code.py` 同理。
- **验证**：
  - **三语言各跑一次 AC**（C++ 用现有题，Python/Java 各配一道）
  - 三语言各跑一次 CE（故意写语法错误），确认 `compile_output` 有内容
  - 三语言各跑一次 TLE（死循环），确认超时被杀
  - **Phase 1 的既有测试全部仍通过**（`pytest -m "not eval_set"`）
- **前置**：PU-15

#### PU-17：前端语言选择器 + 模板

- **产出**：修改 `views/Practice.vue`、`components/CodeEditor.vue`、
  新增 `api/languages.ts`
- **内容**：
  1. 语言下拉选择器，选项来自 `env-check`（PU-3）——
     **不可用的语言禁用并说明原因**（如「未检测到 JDK」）
  2. 切换语言时按 `default_template` 替换编辑器内容（PU-10 的确认框逻辑）
  3. 题目自带默认语言时优先用题目的
- **验证**：切到 Python 能跑通、切到 Java 若未装 JDK 显示禁用态而非报错
- **前置**：PU-3、PU-10、PU-16

---

## 三、切片总览表

| 切片 | 名称 | 组 | 前置 | Agent 预留 |
|---|---|---|---|---|
| PU-0 | Tailwind 验证 + 设计令牌 | A | — | |
| PU-1 | 主进程去窗口化 + 优雅关闭 | A | — | |
| PU-2 | Splash 启动动画 | A | PU-1 | |
| PU-3 | 脚本降级 + 环境自检 | A | PU-1 | ✅ |
| PU-4 | 运行时适配层 | B | — | |
| PU-5 | Web 构建目标 | B | PU-4 | |
| PU-6 | 能力降级 + 响应式 | B | PU-4,5,0 | |
| PU-7 | 应用壳层改版 | C | PU-0,4,6 | |
| PU-8 | 练习页改版 | C | PU-6,7 | |
| PU-9 | 三页统一改版 | C | PU-7 | |
| PU-10 | Monaco 主题 + 语言注册 | C | PU-0,8 | |
| PU-11 | Problem 模型 + TestCase 表 | D | — | ✅ |
| PU-12 | 题库 API | D | PU-11 | ✅ |
| PU-13 | 提交落库 + 链路打通 | D | PU-11,12 | ✅ |
| PU-14 | 20 道题库 + 列表 UI | D | PU-11,12,13 | ✅ |
| PU-15 | 语言描述符层 | E | — | ✅ |
| PU-16 | 编译运行多语言化 | E | PU-15 | ✅ |
| PU-17 | 前端语言选择器 | E | PU-3,10,16 | |

**推荐执行顺序**：PU-0 与 PU-1 并行起步 → PU-2/3 → PU-15/16（后端语言层，
可与前端并行）→ PU-11/12/13/14（题库，后端密集）→ PU-4/5/6（Web）→
PU-7/8/9/10（视觉）→ PU-17 收尾。

> 也可以先做 D 组（题库）再做 C 组（视觉）—— 题库是功能，视觉是体验，
> 功能优先更符合「每步可验证」的原则。**建议顺序：A → E → D → B → C**。
> 即先把「启动体验 + 多语言」打通（这两块验证成本低），再做题库，最后做
> Web 与视觉（视觉改动最大、最容易返工，放在功能稳定后做）。

---

## 四、与 Phase 2（你手写的 Agent）的接口契约

本阶段**刻意**为 Phase 2 准备好以下输入，你手写 Agent 时不需要再倒腾数据：

| Phase 2 需要什么 | 本阶段提供什么 | 出处 |
|---|---|---|
| 真实题目上下文 | `Problem` 全字段 + `TestCase` 表 | PU-11 |
| 隐藏用例（防止 Agent 只针对 sample 分析） | `TestCase.is_sample = False` | PU-11 |
| 判题结果与代码的持久化 | `Submission` 全字段落库 | PU-13 |
| 错因分析的载体 | `MistakeBookEntry`（`error_type` 留 null 待回填） | PU-13 |
| 带标注的评估原料 | `corpus/problems/*.yaml` 的 `correct_reference` + `eval_notes` | PU-14 |
| 多语言分析能力 | `LanguageSpec` + `compile_source(code, language)` | PU-15/16 |
| 环境可行性判断 | `GET /api/v1/env-check` | PU-3 |
| 前端展示 Agent 中间过程的位置 | `OutputPanel.vue` 已改为可插入任意事件类型 | PU-8 |
| 错因筛选 UI 位置 | `MistakesView.vue` 预留禁用态筛选器 | PU-9 |

**你必须自己实现的**（本阶段不动）：
- `sidecar/app/agent/` 全部（schemas / analyzer / state / nodes / graph）
- `sidecar/app/llm/gateway.py`
- `sidecar/app/prompts/`
- `sidecar/app/rag/`
- Phase 2 的 P2-0 ~ P2-22 全部切片

> **边界提醒**：`MistakeBookEntry.error_type` 在 PU-13 会被写成 `null`。
> 你在 P2-4 接入 Agent 后回填它——这正是 VIBECODING.md 中 P1-18 注释
> 「error_type 字段此时为 null，Phase 2 的 P2-4 会接入 AI 错因分析后回填」
> 所描述的设计。本阶段完成后，这个设计承诺**才真正具备落地条件**。

---

## 五、风险与注意事项

### 必须知道的坑

1. **本机中文用户名 + MinGW**：任何新建的临时目录都要避开非 ASCII 路径。
   `TemporaryDirectory` 默认在 `%TEMP%`（含中文），PU-16 必须沿用
   `C:\Windows\Temp` 的 workaround。**这是最容易踩的坑**。

2. **pytest 在 AI 沙箱里的假失败**：若你在 AI 助手环境跑测试，
   `PYTHONPATH` 注入的 `sitecustomize.py` 会在并发清理临时目录时
   `raise SystemExit(1)`。**与项目代码无关**。要用：
   ```
   env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest ../tests -q
   ```
   否则会看到一堆假失败，浪费大量时间排查。

3. **JSON 字段的跨库差异**：`Problem.knowledge_tags` 与 `samples` 是 JSON
   类型，SQLite 与 PostgreSQL 行为不同（尤其 `autogenerate` 的迁移语句）。
   PU-11 的迁移**必须在真实 PG 上验证**。

4. **提交落库的线程边界**：PU-13 的落库发生在 `asyncio.to_thread` 的
   worker thread 里，只能用同步 Session。用 async Session 会得到
   「attached to a different loop」错误——且这个错误在单线程测试里不会
   复现。**必须写集成测试覆盖**。

5. **Java 的 MLE 判定不可靠**：psutil 轮询 RSS 对 JVM 无意义。
   PU-16 需要为 Java 走 `-Xmx` 而非依赖轮询，或放宽 Java 的内存判定。

6. **Web 端 Token 暴露**：`VITE_SIDECAR_TOKEN` 会被打进前端产物。
   PU-5 必须写明这只适用于本机/局域网预览，公网部署需要反代 + 会话鉴权。

7. **Tailwind 可能根本没装**：PU-0 第一步就是核实。若确实没装，
   这是个需要你知晓的事实（AGENTS.md 的约束从未落实），
   而不是悄悄补上。

### 已知但本阶段不修的

- `run_executable` 在 Windows 下对 fork 的子进程处理仍不完善
  （PU-1 只解决 sidecar 本身的进程树，用户程序的子进程树另议）
- ~~项目无 git 仓库~~ **已解决**：仓库已建立在
  `https://gitee.com/xk2b/AlgoForge`（私有），分支 `main`，
  首个基线提交 `c081142`（2026-09-13，131 文件）。
  **本阶段每个切片完成后按 VIBECODING.md 第 7 条立即 commit**
  （`feat(frontend): ...` / `feat(backend): ...`），不要攒一批再提交。

---

## 六、完成检查点

| 检查点 | 时机 | 检查内容 |
|---|---|---|
| 无黑框启动 | PU-3 完成 | 双击启动只见一个窗口，无 cmd/PowerShell |
| Web 可用 | PU-6 完成 | 浏览器能完整走通判题流程 |
| 视觉一致 | PU-10 完成 | 全应用无硬编码色值，状态色统一 |
| 题库自洽 | PU-14 完成 | 20 道题参考解全部 AC，隐藏用例存在 |
| 三语言可用 | PU-17 完成 | C++/Python/Java 各跑通一次 AC |
| 回归无破坏 | 全部完成 | `pytest -m "not eval_set"` 全通过 |

---

## 七、一句话原则

> **本阶段把「能跑的原型」变成「能交付的产品」，并把 Agent 需要的所有
> 输入（题库、隐藏用例、提交记录、语言层、环境探测）准备到位。
> Agent 本身——一行都不写，留给下一阶段。**
