---
id: number-theory
title: 数论基础：约数与素数
knowledge_tags: [数论, 辗转相除, 素数筛, 循环]
related_problems: [p005-prime-sieve, p006-gcd]
common_errors: [off_by_one, boundary_condition, wrong_complexity, overflow]
---

# 数论基础：约数与素数

## 什么时候会用到

题目涉及整除、约数、素数、最大公约数。这一层的错误集中在
**0 和 1 这两个特殊值**，以及**复杂度是否够用**。

## 要点

### 最大公约数：辗转相除法

```
int gcd(int a, int b) {
    while (b != 0) { int t = a % b; a = b; b = t; }
    return a;
}
```

要点：

- **循环条件是 `b != 0`**，不是 `a != b`。
- `gcd(a, 0) = a`，`gcd(0, b) = b`。**0 与任何数的最大公约数是那个数本身**，
  不是 0、也不是 1。
- 递归写法 `return b == 0 ? a : gcd(b, a % b);` 同样正确，
  但**递归深度**在极端输入下可能较大。

对应错因标签：`boundary_condition`。

### 素数判断：0 和 1 不是素数

```
bool isPrime(int n) {
    if (n < 2) return false;              // 这一行不能省
    for (int i = 2; (long long)i * i <= n; i++)
        if (n % i == 0) return false;
    return true;
}
```

两个坑：

1. **循环从 2 开始，所以 0 和 1 根本不进循环，会被误判为素数**——
   必须在开头单独排除。
2. `i * i <= n` 中的乘法可能溢出（`i` 接近 int 上限时），
   强制转成 `long long` 或写成 `i <= n / i`。

对应错因标签：`boundary_condition`、`overflow`。

### 区间内素数个数：用筛法，不要逐个判断

逐个判断的复杂度是 O(n√n)，`n = 10⁶` 时约 10⁹ 步，**必然超时**。
筛法（埃氏筛）复杂度 O(n log log n)，快得多：

```
vector<bool> isComposite(n + 1, false);
for (int i = 2; i * i <= n; i++)
    if (!isComposite[i])
        for (int j = i * i; j <= n; j += i)
            isComposite[j] = true;
```

对应错因标签：`wrong_complexity`。

### 筛法的边界细节

- 数组要开到 `n + 1`，下标才能取到 `n`。
- 内层从 `i * i` 开始（比 `i + i` 更快，且不遗漏）。
- 统计区间 `[L, R]` 的素数时，**两端的包含关系**要看清
  （`L` 和 `R` 本身算不算）。这是 `off_by_one` 的高发区。
- `i * i` 同样有溢出风险，注意类型。

## 常见错误

| 标签 | 表现 |
|---|---|
| `boundary_condition` | 0 和 1 被判成素数；gcd 对 0 的处理不对 |
| `wrong_complexity` | 逐个判断素数，区间大时 TLE |
| `off_by_one` | 统计区间端点算错；数组开小一位 |
| `overflow` | `i * i` 溢出，循环条件失效 |

## 自检清单

- 素数判断里，`n < 2` 的分支写了吗？
- gcd 传 0 进去结果对吗？（`gcd(0, 5)` 应该是 5）
- 区间素数题：复杂度算过吗？需要筛法吗？
- 统计区间的两端包含关系，与题意一致吗？
- 涉及 `i * i` 的地方会溢出吗？
