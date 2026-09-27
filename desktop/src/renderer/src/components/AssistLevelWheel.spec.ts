// @vitest-environment jsdom
/**
 * 辅助强度滚轮（`AssistLevelWheel`）的单元测试。
 *
 * 为什么单独测它
 * --------------
 * `AssistPanel.spec.ts` 是从**面板**的角度验证"点了档位有没有把值写进
 * 服务端"，它只关心事件有没有发出去。滚轮自己有一批与面板无关的行为，
 * 而且都是"错了也不报错"的那种：
 *
 * - 滚动吸附后算出的行号 → 档位号；
 * - 被老师上限卡住的那几档：点击要拦住、滚动经过要弹回；
 * - 键盘上下键要能跳过卡住的行，而不是停在上面；
 * - 「跟着老师走」那一项要发 `null`（不是发上限那个数字）。
 *
 * 最后一条最容易写错且最难发现：发数字会在老师之后改档位时把学生钉死在
 * 旧值上，而这在一周内看不出任何异常。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import AssistLevelWheel from './AssistLevelWheel.vue'
import type { AssistancePolicy } from '../api/assistance'

/** 一档的规则。字段齐全，方便按档号覆盖。 */
function policy(level: number): AssistancePolicy {
  return {
    level,
    label: `L${level}`,
    student_label: `${level} 档`,
    description: `${level} 档的说明`,
    completion_uses_model: level >= 2,
    allows_chat: level >= 3,
    chat_is_hint_only: level < 4,
    allows_code_insertion: level >= 4,
    allows_full_solution: level >= 5
  }
}

const LEVELS: AssistancePolicy[] = [1, 2, 3, 4, 5].map(policy)

function mountWheel(props: Record<string, unknown> = {}) {
  return mount(AssistLevelWheel, {
    props: { levels: LEVELS, modelValue: 5, ...props }
  })
}

/** 项高，必须与组件里的 `ITEM_HEIGHT` 一致。 */
const ITEM = 46

describe('AssistLevelWheel 的渲染', () => {
  it('五档全部画出来，顺序是从 1 到 5', () => {
    const w = mountWheel()
    const nums = w.findAll('.wheel-num').map((n) => n.text())
    expect(nums).toEqual(['1', '2', '3', '4', '5'])
  })

  it('不传 allowDefault 时不出现「跟着老师走」', () => {
    const w = mountWheel()
    expect(w.findAll('.wheel-btn')).toHaveLength(5)
    expect(w.text()).not.toContain('跟着老师走')
  })

  it('传 allowDefault 时多一行「跟着老师走」', () => {
    const w = mountWheel({ allowDefault: true })
    expect(w.findAll('.wheel-btn')).toHaveLength(6)
    expect(w.text()).toContain('跟着老师走')
  })

  it('当前档位那一行带 is-active', () => {
    const w = mountWheel({ modelValue: 3 })
    const active = w.findAll('.wheel-item').filter((i) => i.classes().includes('is-active'))
    expect(active).toHaveLength(1)
    expect(active[0].text()).toContain('3')
  })

  it('modelValue 为 null 时高亮的是「跟着老师走」', () => {
    const w = mountWheel({ modelValue: null, allowDefault: true })
    const active = w.findAll('.wheel-item').filter((i) => i.classes().includes('is-active'))
    expect(active).toHaveLength(1)
    expect(active[0].text()).toContain('跟着老师走')
  })
})

describe('AssistLevelWheel 的上限', () => {
  /**
   * 上限档**本身可选**。
   *
   * 老师设五档的语义是"最多允许到五档"，不是"禁止五档"。判据写成 `<`
   * 会把上限那一档也变成灰的 —— 学生能用的帮助少一档，而界面看起来只是
   * "最高那档不知道为什么点不动"。
   */
  it('上限那一档本身可选，超过它的才禁用', () => {
    const w = mountWheel({ ceiling: 2, modelValue: 2 })
    const btns = w.findAll('.wheel-btn')
    expect(btns[1].attributes('disabled')).toBeUndefined()
    expect(btns[2].attributes('disabled')).toBeDefined()
    expect(btns[4].attributes('disabled')).toBeDefined()
  })

  it('卡住的档位标 is-locked，但仍留在界面上', () => {
    const w = mountWheel({ ceiling: 3 })
    const locked = w.findAll('.wheel-item').filter((i) => i.classes().includes('is-locked'))
    expect(locked).toHaveLength(2)
    // 留着而不是删掉：学生该看见"老师设了上限"，而不是觉得系统少给了选项。
    expect(w.findAll('.wheel-btn')).toHaveLength(5)
  })

  it('上限等于最高档时不画「被限制」那句说明', () => {
    const w = mountWheel({ ceiling: 5 })
    expect(w.text()).not.toContain('更高的档位不可选')
  })

  it('上限低于最高档时说明卡在第几档', () => {
    const w = mountWheel({ ceiling: 3 })
    expect(w.text()).toContain('3 档')
    expect(w.text()).toContain('更高的档位不可选')
  })

  it('点被卡住的档位不发事件', async () => {
    const w = mountWheel({ ceiling: 2, modelValue: 1 })
    await w.findAll('.wheel-btn')[3].trigger('click')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('disabled 时点任何一档都不发事件', async () => {
    const w = mountWheel({ modelValue: 1, disabled: true })
    await w.findAll('.wheel-btn')[2].trigger('click')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })
})

/**
 * 滚动停止后按居中的那一行写回。
 *
 * 这里直接改 `scrollTop` 再触发 `scroll`，模拟"学生滚了一下"。行高是
 * 46px，滚到第 3 行（index 2，也就是三档）就是 92。
 *
 * 用假定时器跳过 140ms 的 debounce：`scrollend` 在 Electron 33 的内核上
 * 不保证有，所以实现用的是 debounce，测试也得跟着等它。
 *
 * 两个 jsdom 的坑：
 * 1. **没有布局引擎**，`el.scrollTop = 92` 会被静默丢掉（读回来还是 0）,
 *    所以得用 `defineProperty` 装一个真的存得住的属性。
 * 2. **`onMounted` 里的 `centerOn` 会先写一次 `scrollTop`**（把当前档位
 *    滚到中间）。stub 必须在它之后装 —— 装在前面会被那次赋值覆盖掉，
 *    测出来的就是"滚到当前档位"而不是"滚到三档"。所以要 `await nextTick()`。
 */
async function scrollTo(w: VueWrapper, itemIndex: number): Promise<void> {
  await nextTick()
  const track = w.find('.wheel-track')
  Object.defineProperty(track.element, 'scrollTop', {
    value: itemIndex * ITEM,
    writable: true,
    configurable: true
  })
  await track.trigger('scroll')
}

describe('AssistLevelWheel 的交互', () => {
  it('点某一档就把这个数字发出去', async () => {
    const w = mountWheel({ modelValue: 5 })
    await w.findAll('.wheel-btn')[1].trigger('click')
    expect(w.emitted('update:modelValue')?.[0]).toEqual([2])
  })

  it('点「跟着老师走」发的是 null，不是老师那个数字', async () => {
    const w = mountWheel({ modelValue: 3, allowDefault: true, ceiling: 5 })
    await w.findAll('.wheel-btn')[5].trigger('click')
    // 发 5 会把学生钉在老师当时那一档上；发 null 才是"我不单独指定"。
    expect(w.emitted('update:modelValue')?.[0]).toEqual([null])
  })

  it('点已经选中的那一档不发事件 —— 免得白写一次库', async () => {
    const w = mountWheel({ modelValue: 3 })
    await w.findAll('.wheel-btn')[2].trigger('click')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('上下键换档', async () => {
    const w = mountWheel({ modelValue: 2 })
    const track = w.find('.wheel-track')
    await track.trigger('keydown', { key: 'ArrowDown' })
    expect(w.emitted('update:modelValue')?.[0]).toEqual([3])

    const w2 = mountWheel({ modelValue: 3 })
    await w2.find('.wheel-track').trigger('keydown', { key: 'ArrowUp' })
    expect(w2.emitted('update:modelValue')?.[0]).toEqual([2])
  })

  /**
   * 键要能"跨过"被卡住的行。
   *
   * 停在卡住的那一档上，学生会以为键盘坏了 —— 他能看见那一档、按了键却
   * 什么都没变。跳过它落到"真的能选"的下一档才符合预期。
   */
  it('上下键跳过被上限卡住的档位', async () => {
    const w = mountWheel({ modelValue: 1, ceiling: 2 })
    await w.find('.wheel-track').trigger('keydown', { key: 'ArrowDown' })
    expect(w.emitted('update:modelValue')?.[0]).toEqual([2])

    // 再往下没有可选项了，不发事件。
    const w2 = mountWheel({ modelValue: 2, ceiling: 2 })
    await w2.find('.wheel-track').trigger('keydown', { key: 'ArrowDown' })
    expect(w2.emitted('update:modelValue')).toBeUndefined()
  })

  it('别的键不管 —— 免得把 Tab / F5 吃掉', async () => {
    const w = mountWheel({ modelValue: 2 })
    await w.find('.wheel-track').trigger('keydown', { key: 'Tab' })
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('滚动停止后按居中那一行写回', async () => {
    vi.useFakeTimers()
    try {
      const w = mountWheel({ modelValue: 5 })
      // index 2 = 三档。
      await scrollTo(w, 2)
      vi.advanceTimersByTime(200)
      expect(w.emitted('update:modelValue')?.[0]).toEqual([3])
    } finally {
      vi.useRealTimers()
    }
  })

  it('滚到被卡住的档位时弹回原位，不静默改档', async () => {
    vi.useFakeTimers()
    try {
      const w = mountWheel({ modelValue: 1, ceiling: 1 })
      await scrollTo(w, 3)
      vi.advanceTimersByTime(200)
      // 没有发事件 —— 学生没要求换档，是惯性把他带过去的。
      expect(w.emitted('update:modelValue')).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('AssistLevelWheel 的说明文字', () => {
  it('选中某档时说明跟着换', () => {
    const w = mountWheel({ modelValue: 4 })
    expect(w.find('.wheel-summary').text()).toContain('4 档的说明')
  })

  it('跟着老师走时说明写清"按默认几档执行"', () => {
    const w = mountWheel({ modelValue: null, allowDefault: true, defaultLevel: 3 })
    expect(w.find('.wheel-summary').text()).toContain('跟着老师走')
    expect(w.find('.wheel-summary').text()).toContain('3 档')
  })

  it('档位表是空的时候不炸，只是没有可选项', () => {
    const w = mountWheel({ levels: [] })
    expect(w.findAll('.wheel-btn')).toHaveLength(0)
    expect(w.find('.wheel-summary').exists()).toBe(true)
  })
})
