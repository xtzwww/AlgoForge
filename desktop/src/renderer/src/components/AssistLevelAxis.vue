<script setup lang="ts">
/**
 * 横向刻度轴辅助强度选择器（2026-09-27）。
 *
 * 与 `AssistLevelWheel`（纵向滚轮）的关系
 * --------------------------------------
 * 滚轮是旧版学生端控件，被一个按钮收在后面、点开才弹出。用户反馈它"像个
 * 弹窗、不直观"，要求把强度画成一条**横向的轴**、有科技感 —— 一档到五档
 * 就是一根能量条从弱到强，当前档位是条上发光的那个点。
 *
 * 组件只发事件、不改状态：学生端要落库，老师端要提交表单，两条路不同。
 * 接口与滚轮完全一致（levels / modelValue / ceiling / allowDefault /
 * defaultLevel / disabled），替换调用方时不需要改父组件的逻辑。
 *
 * 为什么横向够用
 * --------------
 * 旧版担心"横向五格每格 50px、档位名放不下"。这版把档位名改成只显示
 * `student_label`（"给提示"），说明文字挪到轴下方的 summary 行 —— 轴上
 * 只留「数字 + 短名」，350px 面板里每格 60px 放得下。
 */
import { computed } from 'vue'
import type { AssistancePolicy } from '../api/assistance'

const props = withDefaults(
  defineProps<{
    /** 五档规则，由 `/assistance/levels` 提供。空数组时渲染骨架。 */
    levels: AssistancePolicy[]
    /** 当前选中的档位。`null` 表示"用服务端默认"，见 `allowDefault`。 */
    modelValue: number | null
    /** 可选的上限。高于它的档位禁用。 */
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

/** 不透传未声明的属性。理由同 `AssistLevelWheel`。 */
defineOptions({ inheritAttrs: false })

/** 按档号升序：1 在最左、5 在最右，"往右 = 帮助更多"。 */
const ordered = computed<AssistancePolicy[]>(() =>
  [...props.levels].sort((a, b) => a.level - b.level)
)

/** 某一档是否可选。上界用 `<=`：老师设五档时五档本身可选。 */
function isSelectable(level: number): boolean {
  if (props.disabled) return false
  if (props.ceiling === null || props.ceiling === undefined) return true
  return level <= props.ceiling
}

/** 当前值在第几个刻度（0 起）。没选中时回到第一个。 */
const activeIndex = computed<number>(() => {
  const v = props.modelValue
  if (v === null) return 0
  const i = ordered.value.findIndex((p) => p.level === v)
  return i < 0 ? 0 : i
})

/**
 * 能量条填充宽度（0% – 100%）。
 *
 * 从最左刻度到当前刻度的发光渐变条，宽度 = 当前索引占整根轴的百分比。
 * `allowDefault` 只影响轴上方多一个"跟随老师"的芯片，不影响刻度条本身。
 */
const fillPercent = computed<number>(() => {
  const n = ordered.value.length
  if (n === 0) return 0
  return (activeIndex.value / (n - 1)) * 100
})

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

function pick(level: number | null): void {
  if (props.disabled) return
  if (level !== null && !isSelectable(level)) return
  if (level === props.modelValue) return
  emit('update:modelValue', level)
}

/** 键盘：左右键换档，跳过被上限卡住的可选。 */
function onKeydown(e: KeyboardEvent): void {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
  e.preventDefault()
  if (props.disabled) return
  const step = e.key === 'ArrowRight' ? 1 : -1
  let next = activeIndex.value + step
  while (next >= 0 && next < ordered.value.length) {
    const level = ordered.value[next]?.level
    if (level !== undefined && isSelectable(level)) break
    next += step
  }
  if (next < 0 || next >= ordered.value.length) return
  pick(ordered.value[next]!.level)
}
</script>

<template>
  <div class="axis" :class="{ 'is-disabled': disabled }">
    <button
      v-if="allowDefault"
      type="button"
      class="axis-default"
      :class="{ 'is-active': modelValue === null }"
      :disabled="disabled"
      @click="pick(null)"
    >
      <span class="axis-default-ring" aria-hidden="true"></span>
      跟随老师走<template v-if="defaultLevel">（默认 {{ defaultLevel }} 档）</template>
    </button>

    <div
      class="axis-track"
      role="radiogroup"
      aria-label="AI 辅助强度"
      tabindex="0"
      @keydown="onKeydown"
    >
      <!-- 底轨：整根轴的底色。 -->
      <div class="axis-rail" aria-hidden="true"></div>
      <!-- 能量条：从最左到当前档的发光填充。 -->
      <div class="axis-fill" aria-hidden="true" :style="{ width: `${fillPercent}%` }"></div>

      <div
        v-for="p in ordered"
        :key="p.level"
        class="axis-step"
        :class="{
          'is-active': p.level === modelValue,
          'is-locked': !isSelectable(p.level)
        }"
        role="radio"
        :aria-checked="p.level === modelValue"
      >
        <button
          type="button"
          class="axis-btn"
          :disabled="!isSelectable(p.level)"
          :title="
            !isSelectable(p.level)
              ? `老师把这次作业限制在 ${ceiling} 档`
              : `${p.label} — ${p.description}`
          "
          @click="pick(p.level)"
        >
          <span class="axis-dot" aria-hidden="true"></span>
          <span class="axis-num">{{ p.level }}</span>
          <span class="axis-name">{{ p.student_label }}</span>
        </button>
      </div>
    </div>

    <p class="axis-summary">
      <template v-if="modelValue !== null && currentPolicy">
        <strong>{{ currentPolicy.label }}</strong>
        —— {{ summary }}
      </template>
      <template v-else>{{ summary }}</template>
    </p>

    <p v-if="hasCeilingCut" class="axis-ceiling">
      老师把这次作业限制在 <strong>{{ ceiling }} 档</strong>，更高的档位不可选。
    </p>
  </div>
</template>

<style scoped>
.axis {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 跟随老师：轴上方的一个小芯片，选中时亮起主题色。 */
.axis-default {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--af-text-muted);
  background: var(--af-elevated);
  border: 1px solid var(--af-border);
  border-radius: 999px;
  cursor: pointer;
  transition:
    color 0.15s,
    border-color 0.15s,
    box-shadow 0.15s;
}

.axis-default:hover:not(:disabled) {
  color: var(--af-text-primary);
  border-color: var(--af-border-strong);
}

.axis-default.is-active {
  color: var(--af-accent);
  border-color: var(--af-accent);
  box-shadow: 0 0 8px color-mix(in srgb, var(--af-accent) 35%, transparent);
}

.axis-default-ring {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

/*
  刻度轨。
  底轨和能量条绝对定位在同一水平线上（top: 15px，高 3px），刻度按钮
  压在上面。`.axis-fill` 的宽度由脚本按当前档算好 —— 这是"科技感"的
  主体：强度越强，亮条越长。
*/
.axis-track {
  position: relative;
  display: flex;
  justify-content: space-between;
  padding: 0 1px;
  outline: none;
}

.axis-track:focus-visible {
  border-radius: var(--af-radius-sm);
  outline: 2px solid var(--af-accent);
  outline-offset: 2px;
}

.axis-rail {
  position: absolute;
  top: 15px;
  right: 0;
  left: 0;
  height: 3px;
  border-radius: 999px;
  background: var(--af-border);
}

.axis-fill {
  position: absolute;
  top: 15px;
  left: 0;
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--af-accent) 55%, transparent), var(--af-accent));
  box-shadow: 0 0 8px color-mix(in srgb, var(--af-accent) 55%, transparent);
  transition: width 0.18s ease;
  pointer-events: none;
}

/*
  每个刻度：圆点（骑在轨道线上）+ 档号 + 短名。
  按钮内容整块可点；禁用档（被老师上限卡住）画成虚线圆点。
*/
.axis-step {
  position: relative;
  flex: 1 1 0;
}

.axis-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 0;
  font: inherit;
  color: var(--af-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
}

.axis-btn:disabled {
  cursor: not-allowed;
}

.axis-dot {
  width: 9px;
  height: 9px;
  margin-top: 12px; /* 与轨道线（top 15 + 高 3 / 2）中心对齐 */
  border-radius: 50%;
  background: var(--af-surface);
  border: 2px solid var(--af-border-strong);
  box-sizing: border-box;
  transition:
    background 0.15s,
    border-color 0.15s,
    box-shadow 0.15s,
    transform 0.15s;
}

/* 当前档：圆点换成主题色并发光，档号同色。 */
.axis-step.is-active .axis-dot {
  background: var(--af-accent);
  border-color: var(--af-accent);
  box-shadow:
    0 0 10px var(--af-accent),
    0 0 3px var(--af-accent);
  transform: scale(1.15);
}

.axis-step.is-active .axis-num {
  color: var(--af-accent);
  text-shadow: 0 0 8px color-mix(in srgb, var(--af-accent) 70%, transparent);
}

.axis-step.is-locked .axis-dot {
  border-style: dashed;
  opacity: 0.45;
}

.axis-step.is-locked .axis-btn {
  opacity: 0.55;
}

.axis-num {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  transition: color 0.15s;
}

.axis-name {
  max-width: 100%;
  font-size: 10px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.axis-summary {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.axis-summary strong {
  color: var(--af-text-primary);
}

.axis-ceiling {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--af-text-muted);
}

.axis-ceiling strong {
  color: var(--af-text-primary);
}

.axis.is-disabled {
  opacity: 0.6;
  pointer-events: none;
}
</style>
