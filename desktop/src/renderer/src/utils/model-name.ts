/**
 * 模型短名：把「agnes-3.0-flash」压成「agnes3.0f」。
 *
 * 面板的工具条只有 ~340px 宽，完整模型名在「模型」按钮里放不下；短名
 * 保住最有用的两段信息——**厂商**和**版本号**，后缀取各段首字母缩写。
 *
 * 规则：
 *
 *   - 按 `-` 切分；第二段以数字开头视为版本段；
 *   - 有版本段 → `厂商 + 版本 + 其余段首字母`（小写）；
 *   - 没有版本段（deepseek-coder、qwen2.5-coder:7b）→ 原样返回。缩写
 *     会把一个认识的名字变成看不懂的乱码，不值得。
 *
 * 纯展示用，不影响发出去的请求（请求仍用完整 model 名）。
 */
export function shortModelName(model: string): string {
  const name = model.trim()
  if (name === '') return name
  const parts = name.split('-')
  if (parts.length < 2) return name
  const version = parts[1]
  if (!/^\d/.test(version)) return name
  const suffix = parts
    .slice(2)
    .map((p) => p.charAt(0))
    .join('')
    .toLowerCase()
  return `${parts[0]}${version}${suffix}`
}
