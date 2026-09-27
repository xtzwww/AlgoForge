<script setup lang="ts">
/**
 * 纵向滚轮辅助强度选择器（2026-09-26）。
 *
 * 与 `AssistLevelPicker`（横向刻度尺）的关系
 * ------------------------------------------
 * 刻度尺是给**老师端**的：老师要一眼看到"一共五档"，横向铺开最直观，
 * 而且他有整页宽度。
 *
 * 这个滚轮是给**学生端**的：它被收在一个按钮后面，点开才出现。面板只有
 * 300 多像素宽，横向五格会把每格压到 50px 出头、档位名（"AI 代做"）根本
 * 放不下；纵向排则每一行都有整行宽度写名字和说明。滚轮式交互还有个好处
 * ——它天然表达了"从低到高是一根轴"，中间的当前值居中被高亮。
 *
 * 为什么不是 `<NSelect>` 或原生 `<select>`
 * ---------------------------------------
 * 两者都把"一共有几档"藏起来，而这正是上一版被投诉的原因（老师设了五档，
 * 学生看到的却像只有三档）。滚轮把五档全部画出来，被老师上限卡住的档位
 * 画成虚线并标出原因 —— 学生看到的是"存在但不可选"，而不是"凭空少了
 * 一个选项"。
 *
 * 为什么滚动也生效
 * ----------------
 * 只让点击选中、拦掉滚轮，会让人本能地以为可以拨 —— 一个看起来能转但
 * 转不动的东西比不能转的更挫败。所以滚轮**真的滚**，用 `scroll-snap`
 * 吸附到整数档，停止滚动后把最近的一档写回。
 *
 * 组件只发事件、不改状态：学生端要落库，老师端要提交表单，两条路不同。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AssistancePolicy } from '../api/assistance'

const props = withDefaults(
  defineProps<{
    /** 五档规则，由 `/assistance/levels` 提供。空数组时渲染骨架。 */
    levels: AssistancePolicy[]
    /** 当前选中的档位。`null` 表示"用服务端默认"，见 `allowDefault`。 */
    modelValue: number | null
    /**
     * 可选的上限。学生端传老师设的档位；老师端不传（等于全部可选）。
     * 高于它的档位禁用。
     */
    ceiling?: number | null
    /** 是否显示"跟着老师走（默认）"这一项。学生端需要，老师端不需要。 */
    allowDefault?: boolean
    /** 服务端默认档位，用于「使用默认（N 档）」的标签。 */
    defaultLevel?: number | null
    disabled?: boolean
  }>(),
  {
    ceiling: null,
    allowDefault: false,
    defaultLevel: null,
    disabled: false
  }
)

const emit = defineEmits<{ (e: 'update:modelValue', value: number | null): void }>()

/**
 * 不透传未声明的属性。理由同 `AssistLevelPicker`：父面板可能把与 DOM
 * 保留名同名的属性透传下来（`prefix` 是只读的），写它会抛 TypeError。
 */
defineOptions({ inheritAttrs: false })

/** 按档号升序：1 在最上面、5 在最下面，"往下转 = 帮助更多"。 */
const ordered = computed<AssistancePolicy[]>(() =>
  [...props.levels].sort((a, b) => a.level - b.level)
)

/**
 * 某一档是否可选。
 *
 * 上界判定用 `<=`：老师设五档时五档本身是**可选**的 —— 他是"最多允许到
 * 五档"，不是"禁止五档"。写错会把上限档本身变成灰的，学生能用的帮助
 * 少一档，而界面看起来只是"最高那档不知道为什么点不动"。
 */
function isSelectable(level: number): boolean {
  if (props.disabled) return false
  if (props.ceiling === null || props.ceiling === undefined) return true
  return level <= props.ceiling
}

/** 滚动容器。吸附停止后据此读回居中那一档。 */
const wheelEl = ref<HTMLElement | null>(null)

/**
 * 每一项的固定高度。
 *
 * 必须与 CSS 里的行高**逐像素一致**：回读选中项用的是
 * `scrollTop / ITEM_HEIGHT` 取整，CSS 改了这里不改，滚轮就会选错一档
 * ——而且只在滚动时错、点击时对，最难查的那种。
 */
const ITEM_HEIGHT = 46

/** 一行（含"跟随老师"那行）的档位序号：默认行用 0 表示。 */
function rowLevel(index: number): number | null {
  if (props.allowDefault && index === 0) return null
  const offset = props.allowDefault ? 1 : 0
  return ordered.value[index - offset]?.level ?? null
}

const rowCount = computed<number>(() => ordered.value.length + (props.allowDefault ? 1 : 0))

/** 当前值落在第几行。找不到（比如还没取到档位）时回到第 0 行。 */
const activeIndex = computed<number>(() => {
  const v = props.modelValue
  if (v === null) return props.allowDefault ? 0 : 0
  const i = ordered.value.findIndex((p) => p.level === v)
  if (i < 0) return 0
  return i + (props.allowDefault ? 1 : 0)
})

/**
 * 把某一档滚到正中间。
 *
 * `behavior: 'smooth'` 在测试环境（jsdom）里没有实现，会退化成瞬移，
 * 这不影响断言 —— 断言看的是 `scrollTop` 这个数值本身。
 */
async function centerOn(index: number): Promise<void> {
  await nextTick()
  const el = wheelEl.value
  if (!el) return
  el.scrollTop = index * ITEM_HEIGHT
}

/**
 * 打开时才定位，组件挂载时不定位。
 *
 * 弹出层是 `v-if` 渲染的，展开动画还没跑完就设 `scrollTop`，浏览器会
 * 在布局归零后把它重置回 0 —— 于是每次打开都停在第一档，而不是当前档。
 */
watch(
  () => props.modelValue,
  () => {
    void centerOn(activeIndex.value)
  }
)

/**
 * 滚动停止后把居中那一档写回。
 *
 * 用 debounce 而不是 `scrollend`：`scrollend` 在 Chrome 114+ 才有，
 * 而 Electron 33 的内核版本不保证；debounce 到处都能用，代价是停止
 * 滚动后有一小段延迟才落定。
 */
let settleTimer: ReturnType<typeof setTimeout> | null = null

function onScroll(): void {
  if (settleTimer !== null) clearTimeout(settleTimer)
  settleTimer = setTimeout(() => {
    settleTimer = null
    commitCentered()
  }, 140)
}

function commitCentered(): void {
  const el = wheelEl.value
  if (!el) return
  const index = Math.round(el.scrollTop / ITEM_HEIGHT)
  const clamped = Math.max(0, Math.min(rowCount.value - 1, index))
  const level = rowLevel(clamped)
  if (level === props.modelValue) return
  // 滚到被上限卡住的档位时退回：不静默把学生拖到他调不了的档位。
  if (level !== null && !isSelectable(level)) {
    void centerOn(activeIndex.value)
    return
  }
  if (props.disabled) return
  emit('update:modelValue', level)
}

function pick(index: number): void {
  if (props.disabled) return
  const level = rowLevel(index)
  if (level !== null && !isSelectable(level)) return
  void centerOn(index)
  if (level === props.modelValue) return
  emit('update:modelValue', level)
}

/**
 * 键盘：上下键换档。
 *
 * 滚轮是"看得见但点不准"的控件，键盘用户需要一个确定的步进方式；
 * 上下箭头正是滚轮的语义。
 */
function onKeydown(e: KeyboardEvent): void {
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
  e.preventDefault()
  if (props.disabled) return
  const step = e.key === 'ArrowDown' ? 1 : -1
  let next = activeIndex.value + step
  // 跳过被上限卡住的行：按一次键应该落在"真的能选"的下一档上。
  while (next >= 0 && next < rowCount.value) {
    const level = rowLevel(next)
    if (level === null || isSelectable(level)) break
    next += step
  }
  if (next < 0 || next >= rowCount.value) return
  pick(next)
}

/** 打开时把当前档位滚到中间。 */
onMounted(() => {
  void centerOn(activeIndex.value)
})

onBeforeUnmount(() => {
  if (settleTimer !== null) clearTimeout(settleTimer)
})

/** 一句话说明当前选择。没选时说明"没选"本身，而不是留空。 */
const currentPolicy = computed<AssistancePolicy | null>(
  () => ordered.value.find((p) => p.level === props.modelValue) ?? null
)

const summary = computed<string>(() => {
  if (props.modelValue === null && props.allowDefault) {
    const d = props.defaultLevel
    return d ? `不指定，跟着老师走（当前默认 ${d} 档）。` : '不指定，跟着老师走。'
  }
  return currentPolicy.value?.description ?? '请选择一档。'
})

const hasCeilingCut = computed<boolean>(() => {
  const c = props.ceiling
  if (c === null || c === undefined || ordered.value.length === 0) return false
  return c < ordered.value.length
})
</script>

<template>
  <div class="wheel" :class="{ 'is-disabled': disabled }">
    <!--
      滚轮本体。
      `role="listbox"` 是 ARIA 对"从一组里选一个、且当前项被高亮"这一模式
      的写法；键盘可达性由 `tabindex` + `onKeydown` 承担。

      外面这层 `.wheel-body` 只做定位：`.wheel-frame` 要绝对定位在轨道
      中间，而轨道自己会滚 —— 把它挂在轨道里，框会跟着内容一起滚走。
    -->
    <div class="wheel-body">
    <div
      ref="wheelEl"
      class="wheel-track"
      role="listbox"
      aria-label="AI 辅助强度"
      tabindex="0"
      @scroll="onScroll"
      @keydown="onKeydown"
    >
      <!--
        首尾各留半屏空白，让第一档与最后一档也能滚到正中间 ——
        否则最上/最下那档永远贴在边上，看起来像"滚不到"。
      -->
      <div class="wheel-pad" aria-hidden="true"></div>

      <div
        v-for="(p, i) in ordered"
        :key="p.level"
        class="wheel-item"
        :class="{
          'is-active': p.level === modelValue,
          'is-locked': !isSelectable(p.level)
        }"
        role="option"
        :aria-selected="p.level === modelValue"
      >
        <button
          class="wheel-btn"
          type="button"
          :disabled="!isSelectable(p.level)"
          :title="
            !isSelectable(p.level)
              ? `老师把这次作业限制在 ${ceiling} 档`
              : `${p.label} — ${p.description}`
          "
          @click="pick(i + (allowDefault ? 1 : 0))"
        >
          <span class="wheel-num">{{ p.level }}</span>
          <span class="wheel-text">
            <span class="wheel-name">{{ p.student_label }}</span>
            <span class="wheel-desc">{{ p.description }}</span>
          </span>
        </button>
      </div>

      <div
        v-if="allowDefault"
        class="wheel-item"
        :class="{ 'is-active': modelValue === null }"
        role="option"
        :aria-selected="modelValue === null"
      >
        <button class="wheel-btn" type="button" :disabled="disabled" @click="pick(0)">
          <span class="wheel-num wheel-num-auto" aria-hidden="true">↺</span>
          <span class="wheel-text">
            <span class="wheel-name">跟着老师走</span>
            <span class="wheel-desc">
              {{ defaultLevel ? `不单独指定，按老师的 ${defaultLevel} 档执行` : '不单独指定' }}
            </span>
          </span>
        </button>
      </div>

      <div class="wheel-pad" aria-hidden="true"></div>
    </div>

    <!--
      选中框：固定不动的两条横线，中间的条目滚过它。
      它在轨道**外面**（.wheel-body 的子节点），所以不随滚动位移；
      `pointer-events: none` 保证不挡点击。
    -->
    <div class="wheel-frame" aria-hidden="true"></div>
    </div>

    <p class="wheel-summary">
      <template v-if="modelValue !== null && currentPolicy">
        <strong>{{ currentPolicy.label }}</strong>
        —— {{ summary }}
      </template>
      <template v-else>{{ summary }}</template>
    </p>

    <p v-if="hasCeilingCut" class="wheel-ceiling">
      老师把这次作业限制在 <strong>{{ ceiling }} 档</strong>，更高的档位不可选。
    </p>
  </div>
</template>

<style scoped>
.wheel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 定位层：给 `.wheel-frame` 提供 absolute 的参照，自身不参与视觉。 */
.wheel-body {
  position: relative;
}

/*
  滚轮轨道。
  高度固定为 3 行：中间是选中项，上下各露半行作为"还有更多"的暗示。
  不用 flex 居中，而靠首尾的 `.wheel-pad` 撑——这样 `scrollTop` 与
  行号是严格线性的，删掉 pad 会让第一条永远滚不到中间。
*/
.wheel-track {
  position: relative;
  height: 138px;
  overflow-y: auto;
  padding: 0;
  scroll-snap-type: y mandatory;
  /* 藏掉原生滚动条：它在一个 3 行高的控件里比内容还显眼。 */
  scrollbar-width: none;
}

.wheel-track::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.wheel-track:focus-visible {
  outline: 2px solid var(--af-accent);
  outline-offset: -2px;
  border-radius: var(--af-radius-sm);
}

/* 半屏留白 = 1.5 行，正好让首尾项居中。 */
.wheel-pad {
  height: 69px;
}

.wheel-item {
  height: 46px;
  scroll-snap-align: center;
}

.wheel-btn {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  height: 100%;
  padding: 0 9px;
  font: inherit;
  text-align: left;
  color: var(--af-text-muted);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s,
    color 0.15s;
}

.wheel-btn:hover:not(:disabled) {
  color: var(--af-text-primary);
  background: var(--af-elevated);
}

/* 当前档：整行染色 + 左侧强调条。滚轮里"看得见"比"框得住"更可靠 ——
   外框只有两条线，扫视时容易漏。 */
.wheel-item.is-active .wheel-btn {
  color: var(--af-text-primary);
  background: var(--af-accent-subtle);
  border-color: var(--af-accent);
}

/* 被老师上限卡住的档位：存在、但清楚地说"不可选"。留虚线而不是隐藏 ——
   学生该看见老师设了上限，而不是觉得系统少给了一个选项。 */
.wheel-item.is-locked .wheel-btn {
  opacity: 0.45;
  border-style: dashed;
  border-color: var(--af-border);
  cursor: not-allowed;
}

.wheel-item.is-locked .wheel-btn:hover {
  color: var(--af-text-muted);
  background: transparent;
}

.wheel-num {
  flex-shrink: 0;
  width: 26px;
  font-size: 17px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.wheel-num-auto {
  font-size: 15px;
}

.wheel-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.wheel-name {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.3;
}

.wheel-desc {
  font-size: 10.5px;
  line-height: 1.3;
  color: var(--af-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/*
  固定的选中框。上下内缩 1 行，正好框住中间那行。
  它是 `.wheel-body` 的子节点，所以不随轨道滚动而位移。
*/
.wheel-frame {
  position: absolute;
  right: 0;
  left: 0;
  top: 46px;
  height: 46px;
  pointer-events: none;
  border-top: 1px solid var(--af-border-strong);
  border-bottom: 1px solid var(--af-border-strong);
  border-radius: var(--af-radius-sm);
}

.wheel-summary {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.wheel-summary strong {
  color: var(--af-text-primary);
}

.wheel-ceiling {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.wheel-ceiling strong {
  color: var(--af-text-primary);
}

.wheel.is-disabled {
  opacity: 0.6;
  pointer-events: none;
}
</style>
