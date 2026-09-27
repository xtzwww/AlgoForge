<script setup lang="ts">
/**
 * 五档辅助强度选择器（2026-09-26）。
 *
 * 为什么不是下拉框
 * ----------------
 * 原来老师端是一个 `<NSelect>`。收起来时只看得到一个值，老师要展开才知道
 * "一共有几档"——于是"五档"这件事在界面上根本不可见，用户报的就是"我设了
 * 五档怎么还是三档"（实际是下拉当时只渲染出了三档的观感）。
 *
 * 改成一条**可见的刻度尺**：一到五全部画出来，当前值高亮。老师不需要任何
 * 交互就能看到档位数，也不需要展开就能比较。
 *
 * 为什么用离散刻度而不是滑杆
 * --------------------------
 * 五档是有明确语义的离散档（见 `app/assistance.py`），不是可连续取值的
 * 量。做成连续滑杆会暗示"3.5 档"是合法的，而它在服务端会被 `normalize_level`
 * 收拢成 3——界面许下一个做不到的承诺。所以拖动时吸附到整数刻度。
 *
 * 上限（ceiling）的两种含义
 * ------------------------
 * - 老师端：不传 `ceiling`，五个档全部可选。
 * - 学生端：传 `ceiling`，**高于上限的档渲染成禁用**，并在刻度上画一条
 *   分界线。学生能看出"这件事被老师卡住了"，而不是"A 选项不知道为什么
 *   点不动"。
 *
 * 组件只发出事件，不改任何状态：老师端要提交表单、学生端要落库，两条
 * 路径不同，选择器不该替它们决定。
 */
import { computed } from 'vue'
import type { AssistancePolicy } from '../api/assistance'

const props = withDefaults(
  defineProps<{
    /** 五档规则，由 `/assistance/levels` 提供。空数组时渲染骨架。 */
    levels: AssistancePolicy[]
    /** 当前选中的档位。`null` 表示"用默认"，见 `allowDefault`。 */
    modelValue: number | null
    /**
     * 可选的上限。学生端传老师设的档位；老师端不传（等于全部可选）。
     * 高于它的刻度会禁用。
     */
    ceiling?: number | null
    /**
     * 是否显示"用服务端默认"这一项。老师发表单需要它（不选=让服务端定），
     * 学生端不需要（他已经有明确的上限）。
     */
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
 * 不透传未声明的属性。
 *
 * `prefix` 会在 Vue 的属性透传链里落到这里 —— 父面板收了一个同名 prop，
 * 而它自己也是某个组件的子节点时，未消费的属性会继续往下传。`prefix` 是
 * DOM 的**只读原生属性**，`patchDOMProp` 写它会抛
 * `TypeError: Cannot set property prefix of [object Element] which has only a getter`，
 * 每次挂载都留一条 Vue 警告。本组件的根是固定的 `<div class="level-picker">`，
 * 不需要接收外部属性。
 */
defineOptions({ inheritAttrs: false })

/** 刻度按档号升序，保证 1 在左、5 在右，与"越往右帮助越多"一致。 */
const ordered = computed<AssistancePolicy[]>(() =>
  [...props.levels].sort((a, b) => a.level - b.level)
)

/**
 * 某一档是否对学生可选。
 *
 * 上界判定用 `<=`：老师设五档时，五档本身是**可选**的——他是"最多允许到
 * 五档"，不是"禁止五档"。这条边界写错会把上限档本身变成不可选，学生能
 * 用的帮助就少了一档，而界面看起来只是"最高那档是灰的"。
 */
function isSelectable(level: number): boolean {
  if (props.disabled) return false
  if (props.ceiling === null || props.ceiling === undefined) return true
  return level <= props.ceiling
}

const current = computed<AssistancePolicy | null>(
  () => ordered.value.find((p) => p.level === props.modelValue) ?? null
)

/**
 * 刻度尺上那条"到这里为止"的分界线位置。
 *
 * 只在真的有档位被卡住时才画：上限等于最高档时画线等于说"你被限制了"，
 * 但实际上没有任何东西被限制，那是误导。
 */
const ceilingMarkPercent = computed<number | null>(() => {
  const n = ordered.value.length
  const c = props.ceiling
  if (n === 0 || c === null || c === undefined) return null
  if (c >= n) return null
  // 每个档位占一个等宽格子，分界线画在第 c 格的右边缘。
  return (c / n) * 100
})

/** 一句话说明当前选择。没选时说明"没选"本身，而不是留空。 */
const summary = computed<string>(() => {
  if (props.modelValue === null && props.allowDefault) {
    const d = props.defaultLevel
    return d ? `不指定，由服务端按默认（${d} 档）执行。` : '不指定，由服务端按默认执行。'
  }
  if (current.value) return current.value.description
  return '请选择一档。'
})

function pick(level: number): void {
  if (!isSelectable(level)) return
  emit('update:modelValue', level)
}

function pickDefault(): void {
  if (props.disabled) return
  emit('update:modelValue', null)
}
</script>

<template>
  <div class="level-picker" :class="{ 'is-disabled': disabled }">
    <!--
      刻度尺本体。用 button 而不是 div + click：键盘可达、焦点可见、
      屏幕阅读器能读——一条只能点鼠标的选择器会把键盘用户挡在外面。
    -->
    <div class="level-scale" role="radiogroup" aria-label="AI 辅助强度">
      <div
        v-for="p in ordered"
        :key="p.level"
        class="level-step"
        :class="{
          'is-active': p.level === modelValue,
          'is-locked': !isSelectable(p.level)
        }"
      >
        <button
          class="level-step-btn"
          type="button"
          role="radio"
          :aria-checked="p.level === modelValue"
          :aria-disabled="!isSelectable(p.level)"
          :disabled="!isSelectable(p.level)"
          :title="
            !isSelectable(p.level)
              ? `老师把这次作业限制在 ${ceiling} 档`
              : `${p.label} — ${p.description}`
          "
          @click="pick(p.level)"
        >
          <span class="level-num">{{ p.level }}</span>
          <span class="level-name">{{ p.student_label }}</span>
        </button>
      </div>

      <!-- 分界线：只在真的有档位被卡住时才画，见 ceilingMarkPercent。 -->
      <span
        v-if="ceilingMarkPercent !== null"
        class="level-ceiling-mark"
        :style="{ left: ceilingMarkPercent + '%' }"
        aria-hidden="true"
      ></span>
      <!-- 连接刻度的一条底线，让五个格子读起来像一把尺子。 -->
      <span class="level-rail" aria-hidden="true"></span>
    </div>

    <p class="level-summary">
      <template v-if="modelValue !== null && current">
        <strong>{{ current.label }}</strong>
        —— {{ summary }}
      </template>
      <template v-else>{{ summary }}</template>
    </p>

    <button
      v-if="allowDefault && modelValue !== null"
      class="level-reset"
      type="button"
      :disabled="disabled"
      @click="pickDefault"
    >
      改回「使用默认」
    </button>

    <p v-if="ceiling !== null && ceiling !== undefined" class="level-ceiling-note">
      老师把这次作业的辅助强度限制在 <strong>{{ ceiling }} 档</strong>，更高的档位不可选。
    </p>
  </div>
</template>

<style scoped>
.level-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.level-scale {
  position: relative;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 4px;
  padding-bottom: 6px;
}

/* 尺子底线。压在各格子下方，视觉上把五个档连成一条轴。 */
.level-rail {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 2px;
  background: var(--af-border);
  border-radius: 1px;
}

.level-step {
  position: relative;
}

.level-step-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  width: 100%;
  padding: 8px 4px;
  font: inherit;
  color: var(--af-text-muted);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: var(--af-radius-sm);
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s,
    color 0.15s;
}

.level-step-btn:hover:not(:disabled) {
  color: var(--af-text-primary);
  border-color: var(--af-accent);
}

.level-step.is-active .level-step-btn {
  color: var(--af-accent-contrast, #fff);
  background: var(--af-accent);
  border-color: var(--af-accent);
}

/* 被上限卡住的档位：清晰可辨地"存在但不可选"，而不是消失。
   让学生看见老师卡了几档，比让它们凭空少一个选项更容易理解。 */
.level-step.is-locked .level-step-btn {
  opacity: 0.4;
  cursor: not-allowed;
  background: var(--af-surface);
  border-style: dashed;
}

.level-step.is-locked .level-step-btn:hover {
  color: var(--af-text-muted);
  border-color: var(--af-border);
}

.level-num {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.1;
}

.level-name {
  font-size: 10px;
  line-height: 1.2;
  text-align: center;
}

.level-ceiling-mark {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--af-danger, #d04a4a);
  border-radius: 1px;
  /* 压在格子之上、说明之下，保证在两种主题里都能看见。 */
  z-index: 1;
}

.level-summary {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.level-summary strong {
  color: var(--af-text-primary);
}

.level-reset {
  align-self: flex-start;
  padding: 2px 0;
  font: inherit;
  font-size: 12px;
  color: var(--af-accent);
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
}

.level-reset:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.level-ceiling-note {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.level-ceiling-note strong {
  color: var(--af-text-primary);
}

.level-picker.is-disabled {
  opacity: 0.6;
  pointer-events: none;
}
</style>
