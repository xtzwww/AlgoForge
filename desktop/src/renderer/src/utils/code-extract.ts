/**
 * 从模型回复里取出可编译的代码。
 *
 * 为什么需要单独一层
 * ----------------
 * 五档要的是"完整可运行解法"，但模型几乎总会把代码包在 Markdown 围栏里
 * 再加一两句"思路是……"。直接粘进编辑器会**编译失败**，而报错会指向第一行
 * 那个反引号 —— 学生看到的是"AI 给的代码跑不了"，真相却是它给的是 Markdown
 * 而不是代码。这个模块把那个落差抹掉。
 *
 * 为什么在组件外
 * --------------
 * 纯字符串函数，与 Vue 无关，所以可以单独测、单独复用（对话里顺带出现的
 * 代码走的是同一条路）。放在 `<script setup>` 里会被迫用非导出的写法，而
 * 这正是它值得搬出来的实际理由。
 */

/**
 * 剥掉 Markdown 代码围栏、以及围栏外的说明文字。
 *
 * 处理顺序是刻意的：
 *
 * 1. 有围栏 → **只取围栏里面**的内容。这是最准的一档，因为围栏就是
 *    "这段是代码"的显式标记。
 * 2. 没围栏 → 从第一个像代码开头的地方切起，丢掉前面的中文说明。
 *    比"整段拿去用"准，因为整段会把说明也留成第一行，而那一行是编译错误。
 * 3. 都不匹配 → 原样返回（去掉尾部空白），让调用方决定要不要用。
 */
export function extractCode(text: string): string {
  if (!text) return ''

  const fenced = text.match(/```[a-zA-Z0-9_+-]*\s*\n([\s\S]*?)```/)
  if (fenced && fenced[1].trim() !== '') return fenced[1].replace(/\s+$/, '')

  // 没围栏：从第一个像是代码开头的地方切起。
  const start = text.search(
    /^\s*(?:#include|#define|using namespace|import |from |def |class |public |int main|#!)/m
  )
  if (start > 0) return text.slice(start).replace(/\s+$/, '')
  return text.replace(/\s+$/, '')
}

/**
 * 这段内容看起来是不是一份可编译的完整程序（而不是一句提示）。
 *
 * **判据刻意保守**：只认语言层面的入口特征（`#include` / `int main` /
 * `def` / `public class` …）。宽松一点（比如"含分号就算"）会让一段中文提示
 * 里的示例片段被当成整解，于是**替换掉学生正在写的代码** —— 这是这个函数
 * 唯一一次被用作"能不能覆盖编辑器"的判断，宁可漏判也不要误判。
 */
export function looksLikeProgram(text: string): boolean {
  const code = extractCode(text)
  return /#include|import |def |int main|public class/.test(code)
}
