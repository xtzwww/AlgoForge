# CodeLab 题库外部语料调研报告

**检索时间**：2026-09-24
**数据源**：GitHub REST API（匿名，14 条检索式）+ `raw.githubusercontent.com` 逐文件核对
**调研对象**：CodeLab（毕设 / 原 AlgoForge）的题目语料 `corpus/problems/`
**调研问题**：能否从开源世界找到**可直接复用**的题目语料，来补现有 20 道题？

---

## 0. 结论先行

**有可复用的结构，但几乎没有可复用的中文题面。**

直接结论三条：

1. **最值得拿走的是 `yosupo06/library-checker-problems`（669★, Apache-2.0）**。
   它每道题都是完整的「题面 + 数据生成器 + 标准解 + **故意写错的反例解**」，
   且 `info.toml` 里显式声明反例解的**期望判题结果**（`expect = "WA"`）。
   这和 CodeLab 自己的 `corpus/eval_cases/`（错解 + `expected_root_cause` +
   `observable_verdict`）是**同一个设计思路**，可以借鉴其「反例解清单」的组织方式。

2. **中文题面语料在 GitHub 上基本不存在可直接用的**。
   `题库` 这个中文关键词检索 30 条命中，**几乎全是「面试题库」**（Java/C++/前端八股），
   与算法判题无关。洛谷题面没有任何官方开源，第三方爬虫仓库**授权不明**
   （`earmer/Luogu_answers` 只有 128★ 且无 license），且题目版权属洛谷/出题人。

3. **真正带「题面 + 测试数据」三件套的仓库，授权与体量都不适合直接搬**：
   `DMOJ/online-judge`（1189★）是 AGPL-3.0——**AGPL 会传染**，
   引入到毕设项目有合规风险；`zsgsdesign/NOJ`（944★）MIT 但题库存的是
   南邮校内题目，格式是数据库 dump，转成 YAML 的成本高于自己出题。

**因此推荐路径**：**结构借鉴 Library Checker，题目自编**。
用它的「反例解 + 期望结果」组织法，把现有 20 道题缺的 3 个错因标签补上。
理由在 §5，具体选题在交付的 YAML 文件里。

---

## 1. 圈层模型：先分清「题库」的三个不同层次

用户说「找题库」时，最容易混的是这三个层——它们**不是一回事**：

| 圈层 | 是什么 | 代表 | 对 CodeLab 的价值 |
|---|---|---|---|
| **A. 题面语料** | 题目描述 + 输入输出格式 + 数据范围 | 洛谷题面、Codeforces problemset | ★★★ 直接要的就是这个 |
| **B. 判题数据** | 测试用例（in/out）+ 标准解 + 生成器 | Library Checker、NOJ | ★★★ 判题必需，且能验证题面自洽 |
| **C. 知识讲解** | 算法教程、知识点解释 | OI-Wiki (26735★) | ★★ 对应 `knowledge_cards/`，非题库 |

**关键发现**：GitHub 上 **A 层的中文资源几乎为空白**，B 层有质量极高的
英文仓库（Library Checker），C 层很丰富但 CodeLab 已经有了。

`OI-wiki/OI-wiki` 虽然 26735★，但它是**教程**（`docs/basic`、`docs/dp`、`docs/graph`
这种结构），**不含题面与测试数据**——它对 CodeLab 的价值在
`corpus/knowledge_cards/`（已有 12 张），不在 `corpus/problems/`。这是个典型的层次混淆点。

---

## 2. 最近邻 Top 6（逐个核对，非凭描述）

| 项目 | ★ | 语言 | 授权 | 最近更新 | 与 CodeLab 的重合点 | 它的短板 / 不能用的原因 |
|---|---|---|---|---|---|---|
| **yosupo06/library-checker-problems** | 669 | C++ | **Apache-2.0** | 2026-09-16 | 每题目录：`task.md` + `info.toml` + `sol/`（含 `wa.cpp` 反例）+ 生成器；**显式标注反例解期望结果** | 题面仅英/日双语，**无中文**；题目偏高级（线段树、多项式、图论进阶），难度**远高于** CodeLab 的 easy/medium 档 |
| **DMOJ/online-judge** | 1189 | Python | **AGPL-3.0** | 2026-08-30 | 完整 OJ 平台，含题目模型与判题后端 | ⚠️ **AGPL 传染性授权**，毕设项目不宜引入；题面来自各校/各赛，非自有语料 |
| **zsgsdesign/NOJ**（南邮） | 944 | PHP | MIT | 2024-03-26 | 中文 OJ，**自带题库**（55 MB 仓库） | 题库为**数据库 dump**，非结构化文件；题目为校内自用，未逐题核实授权 |
| **OI-wiki/OI-wiki** | 26735 | TypeScript | — | 2026-09-23 | 中文算法知识讲解，覆盖广 | **是教程不是题库**（见 §1），对 `problems/` 无直接价值 |
| **EndlessCheng/codeforces-go** | 8730 | Go | MIT | 2026-09-23 | 中文注释的竞赛模板与题解 | 是**模板库 + Go 语言题解**，非题面语料；且体量 137 MB |
| **sukhoeing/aoapc-bac2nd-keys** | 591 | C++ | GPL-3.0 | 2021-09-08 | 《算法竞赛入门经典》习题选解 | ⚠️ **只有 .cpp 解法文件**（如 `ch05/UVa10391.cc`），**无题面、无测试数据**；GPL-3.0 且 2021 年后未更新 |

**判定**：这 6 个里，**只有 Library Checker 是「结构可直接借鉴」的**，
其余要么层次不符（OI-Wiki）、要么授权有风险（DMOJ/GPL/AGPL）、
要么只有解没有题（aoapc、codeforces-go）、要么格式需大量转换（NOJ）。

---

## 3. Library Checker 为什么是这次的真正收获

直接看它 `sample/aplusb/` 的真实文件（已逐个拉取核对）：

```
sample/aplusb/
├── task.md          # 题面（英日双语，含 constraints / input / output / sample）
├── info.toml        # 元数据：title / timelimit / [[tests]] / [[solutions]]
├── checker.cpp      # 判题校验器
├── verifier.cpp     # 输入合法性校验
├── gen/             # 数据生成器
└── sol/
    ├── correct.cpp  # 标准解
    ├── ac_func.cpp  # 另一种 AC 写法
    └── wa.cpp       # ★ 故意写错的解
```

`info.toml` 里那两行是关键：

```toml
[[solutions]]
    name = "wa.cpp"
    expect = "WA"      # ← 声明这个解「应该被判 WA」
```

而 `sol/wa.cpp` 的实际内容是**故意把 `a + b` 写成 `(a + b) / 2 * 2`**：

```cpp
int a, b;
scanf("%d %d", &a, &b);
printf("%d\n", (a + b) / 2 * 2);   // 当 a+b 为奇数时与正确答案差 1
```

**这正是 CodeLab `corpus/eval_cases/` 的做法**——`s2-001-max-min.yaml` 里
`wrong_code` 故意写出颠倒的比较、`expected_root_cause: inverted_condition`、
`observable_verdict: WA`，一模一样的三元组。

**可借鉴的具体点**：Library Checker 把「反例解」当作**题目目录的一等公民**，
每个反例解都在 `info.toml` 里挂一个「期望结果」，于是**判题器本身可被自动回归测试**。
CodeLab 目前的反例解（`eval_cases/`）与题库（`problems/`）是**两个独立目录**，
题目文件里只有 `eval_notes` 自由文本描述错因，没有可执行的反例解。

> **一条可以立刻用的改进建议**：给 `corpus/problems/*.yaml` 可选地加
> `wrong_references` 字段（`code` + `expected_label` + `expected_verdict`），
> 让 `tools/load_problems.py` 在验证参考解 AC 的同时，**顺带验证反例解真的 WA**
> ——这样每道题都自带一个「判题器回归用例」。这是从 Library Checker 学到的、
> 且**结构上完全兼容现有 schema**（`CatalogueProblem.extra` 已经在保留未知字段）。

---

## 4. 中文题面：为什么 GitHub 上没有

三条实测证据：

1. **关键词 `题库` 检索 30 条命中，全是面试题**：
   `liyupi/mianshiya`(5906★)、`febobo/web-interview`(11872★)、
   `xiaomabenten/system_architect`(4573★) —— 都是「八股题库」，不是算法判题。
   **中文里「题库」这个词几乎被面试场景占满了**，这本身是个检索陷阱。

2. **洛谷题面没有官方开源**。第三方仓库 `earmer/Luogu_answers`(128★)
   与 `laikit-dev/luogu-saver`(166★) 都是**爬虫/存档工具**，
   授权不明或 AGPL-3.0。**题面版权属洛谷平台与各出题人**，爬取存档不等于获得授权。

3. **`SFOI-Team/luogu-problem-list`(416★)** 只是**题单**（题号列表），
   不含题面——这是另一个容易误判的点。

**合规提醒**：CodeLab 若要引用第三方题面，应学 **BYO（Bring Your Own）模式**
——系统提供导入格式，题目由使用者自行准备，**平台不内置第三方题面**。
现有 `corpus/problems/` 全部标 `source: 经典入门题 / 经典算法题`（自编），
这个做法是**正确且安全**的，应继续保持。

---

## 5. 空白地带与最终建议

| 能力 | Library Checker | DMOJ | NOJ | aoapc | **CodeLab 现有** |
|---|---|---|---|---|---|
| 中文题面 | ❌ | ❌ | ✅ | ❌ | ✅ |
| 结构化题面文件 | ✅ | ✅ | ❌ (DB dump) | ❌ | ✅ YAML |
| 测试数据内嵌 | ✅ 生成器 | ✅ | ✅ | ❌ | ✅ 内嵌 in/out |
| 标准解 | ✅ | ✅ | ✅ | ✅ | ✅ `correct_reference` |
| **反例解 + 期望结果** | ✅ | ❌ | ❌ | ❌ | ✅ 但**在另一个目录** |
| 自研导入校验 | ✅ | — | — | — | ✅ **参考解必须真 AC** |
| 授权可商用 | ✅ Apache | ⚠️ AGPL | ✅ MIT | ⚠️ GPL | — |

**CodeLab 已经具备的差异化**，比外部任何一个都完整：中文题面 + 结构化 +
内嵌数据 + 标准解 + **导入时真的编译跑一遍验证**（`tools/load_problems.py`
「参考解必须 AC，否则整批拒绝导入」——外部仓库没有一个有这道闸门）。

**唯一能补强的地方**：把反例解从 `eval_cases/` 挪进题目文件（或双向关联），
让每道题自带判题器回归用例。这是 §3 那条建议。

**因此，正确答案不是「搬题」，而是「照 Library Checker 的规格自编题」**——
因为：
- 中文题面买不到，自编是唯一可行路径；
- CodeLab 的 schema 比外部更严，搬来的题**也得改写成同样的形状**；
- 现有 20 道题缺的不是数量，是**错因覆盖**（12 个闭集标签有 3 个从未出现）。

---

## 6.5 本轮的落地结果（2026-09-24）

按上面的结论补了 **3 道题**，把 12 个错因标签的覆盖从 **9/12 补到 12/12**：

| 新题 | 难度 | 补的错因 | 验证结果 |
|---|---|---|---|
| `p021-pass-rate-count` 及格人数统计 | easy | **`inverted_condition`** | 参考解 7/7 AC；倒序错解**过全部样例**、隐藏用例 4/5 暴露 |
| `p022-average-of-list` 求平均数 | easy | **`wrong_data_type`** | 参考解 7/7 AC；整除错解**过全部样例**、隐藏用例 4/5/6 暴露 |
| `p023-max-of-queries` 多次询问的最大值 | medium | **`uninitialized_state`** | 参考解 6/6 AC；状态未重置错解**过全部样例**、隐藏用例 4/5/6 暴露 |

题目总数 20 → **23**（easy 9→11 / medium 9→10 / hard 2 不变）。

**一个值得记录的测试设计规律**（本轮实测得出，非推测）：

> 想让某类 bug「过得了样例、栽在隐藏用例」，样例必须满足该 bug 的**不变量**：
>
> | 错因 | 样例要满足的条件 |
> |---|---|
> | `inverted_condition`（`>=` 写成 `<=`） | 样例数据在阈值**两侧成对对称**（如 `50 60 70`：≥60 有 2 个、≤60 也有 2 个） |
> | `wrong_data_type`（整数除法） | 样例的**和恰好能被 n 整除**（如 `1 2 3` 和 6、`3 3 3 3` 和 12） |
> | `uninitialized_state`（状态未重置） | 样例的**后一组结果恰好大于前一组**，即"先小后大" |
>
> 反过来，如果样例是随意挑的，这三类 bug 极容易在样例上就暴露，
> 于是题目失去「能过样例但过不了隐藏用例」这个最有教学价值的性质。
> **这条规律建议写进后续出题的检查清单。**

**顺带发现的三个坑**（都实际踩过）：

1. **判题器真的会抓出题人的算错**。第一次提交时 `-1 5 -1 0 5 3` 的逆序对我算成 6，
   实际是 4，被 `load_problems.py` 判为 `FAIL ... the test cases are wrong, not the solution`。
   **手算嵌套比较极易出错，期望值应当用程序算，不要手写。**
2. **`printf("%.2f", s / n)` 中 `s` 是 `long long` 会触发未定义行为**——
   `%.2f` 期待 `double`，传入整数在 x86-64 上因寄存器不同而读到 0，
   表现为「输出恒为 0.00」，非常像"读入失败"。写错解时要显式 `double avg = sum / n;`。
3. **Bash heredoc 写 C++ 里的 `%d` / `\n` 会被 shell 处理**，
   导致 `scanf` 失败、程序提前 `return 1`，症状是「所有用例都输出 0」。
   写 C++ 文件应当用文件写入工具，不要在 shell 里 heredoc。

---

## 7. 复现方式

**检索式清单**（14 条，全部实跑）：

```
topic:online-judge stars:>200
"online judge" dataset in:name,description stars:>50
"problems" "test data" judge in:name,description stars:>100        → 0 命中
luogu in:name,description stars:>100
洛谷 in:name,description                                            → 30 命中，噪声大
oi-wiki in:name,description stars:>100
题库 in:name,description                                            → 30 命中，几乎全无关
算法题 题解 in:name,description
"算法竞赛" in:name,description
"competitive programming" dataset in:name,description stars:>50     → 1 命中
codecontest in:name,description
leetcode dataset in:name,description stars:>100                     → 噪声
"data structure" exercises in:name,description stars:>100
algorithms textbook exercises in:name,description stars:>200        → 0 命中
```

**反面经验**（避免重走）：

- **`题库` 是中文检索陷阱**：命中全是面试八股。
  带「算法」限定（`算法题 题解`、`算法竞赛`）才有信号，但精度仍低。
- **star 数排序会把无关大仓库排到前面**：`problem-cn` 第一条是
  `fighting41love/funNLP`(83387★)——它只是 readme 里出现了「题库」二字。
  必须逐条看描述，不能只看排名。
- **别忘了 `in:name,description` 不加 `readme`**：加了 readme 噪声会爆炸。
- **精确核对可避免 404**：`hustcc/problem`、`ZJU-ICPC/OnlineJudge`、
  `DMOJ/dmoj` 都是**不存在的**仓库名，凭记忆写必错。
