"""Generate the S2 evaluation set (P2-5).

Writes 20 YAML case files into corpus/eval_cases/. Run once; the files
are then hand-maintained. Kept in the repo so the set can be
regenerated/extended reproducibly.

Every case pairs a buggy submission with a *correct* reference
solution. tools/validate_eval_cases.py then compiles and runs both to
prove (a) the reference ACs all cases and (b) the buggy one does not —
otherwise the "expected root cause" label is not actually supported by
observable evidence.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "corpus" / "eval_cases"

CASES: list[dict] = []


def add(**kw: object) -> None:
    CASES.append(kw)


# --------------------------------------------------------------------------
# 1. boundary_condition — comparison direction wrong, equal inputs hide it
# --------------------------------------------------------------------------
add(
    id="s2-001-max-min",
    title="两数最大值与最小值",
    statement="""
输入两个整数 a 和 b，输出两行：第一行最大值，第二行最小值。
范围 -1000 <= a, b <= 1000。
""",
    tags=["基础语法", "条件分支"],
    difficulty="easy",
    root_cause="boundary_condition",
    verdict="WA",
    notes="""
最小值表达式写成了 `a < b ? b : a`，在 a != b 时返回的是较大者。
a == b 时结果碰巧正确，所以只有不等输入才暴露。典型"比较方向写反"。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int a, b;
    cin >> a >> b;
    int mx = a > b ? a : b;
    int mn = a < b ? b : a;
    cout << mx << endl;
    cout << mn << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    int a, b;
    cin >> a >> b;
    cout << (a > b ? a : b) << endl;
    cout << (a < b ? a : b) << endl;
    return 0;
}
""",
    tests=[("3 7\n", "7\n3\n"), ("7 3\n", "7\n3\n"), ("-5 2\n", "2\n-5\n")],
)

# --------------------------------------------------------------------------
# 2. overflow — int used for a product that exceeds 32-bit
# --------------------------------------------------------------------------
add(
    id="s2-002-sum-product-overflow",
    title="两数乘积",
    statement="""
输入两个整数 a 和 b（1 <= a, b <= 100000），输出 a * b。
""",
    tags=["数据类型", "整数溢出"],
    difficulty="easy",
    root_cause="overflow",
    verdict="WA",
    notes="""
a*b 最大 1e10，超出 int 的 2.1e9 上限。虽然用了 long long 存结果，
但乘法在 int 域内完成后再赋值，已经溢出。小数据（3*7）仍然正确，
所以必须用大数用例才能暴露。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int a, b;
    cin >> a >> b;
    long long product = a * b;
    cout << product << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    long long a, b;
    cin >> a >> b;
    cout << a * b << endl;
    return 0;
}
""",
    tests=[("3 7\n", "21\n"), ("100000 100000\n", "10000000000\n")],
)

# --------------------------------------------------------------------------
# 3. off_by_one — loop bound excludes the last element
# --------------------------------------------------------------------------
add(
    id="s2-003-array-sum-off-by-one",
    title="数组求和",
    statement="""
第一行输入 n（1 <= n <= 100），第二行输入 n 个整数，
输出这 n 个整数的和。
""",
    tags=["数组", "循环边界"],
    difficulty="easy",
    root_cause="off_by_one",
    verdict="WA",
    notes="""
循环写成 `i < n - 1`，少加了最后一个元素。n == 1 时和为 0（也错），
但最直观的暴露方式就是 n >= 2 的普通用例。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[105];
    for (int i = 0; i < n; i++) cin >> a[i];
    long long sum = 0;
    for (int i = 0; i < n - 1; i++) sum += a[i];
    cout << sum << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[105];
    for (int i = 0; i < n; i++) cin >> a[i];
    long long sum = 0;
    for (int i = 0; i < n; i++) sum += a[i];
    cout << sum << endl;
    return 0;
}
""",
    tests=[("3\n1 2 3\n", "6\n"), ("1\n5\n", "5\n"), ("4\n10 20 30 40\n", "100\n")],
)

# --------------------------------------------------------------------------
# 4. wrong_algorithm — greedy where DP is required (coin change)
# --------------------------------------------------------------------------
add(
    id="s2-004-coin-change-greedy",
    title="凑出最少硬币数",
    statement="""
给定面额集合和目标金额 amount，每种硬币数量不限，
输出凑出 amount 所需的最少硬币数，无法凑出输出 -1。
""",
    tags=["动态规划", "贪心"],
    difficulty="hard",
    root_cause="wrong_algorithm",
    verdict="WA",
    notes="""
贪心（每次取最大面额）对任意面额集合不成立。经典反例：coins=[1,3,4]，
amount=6 时贪心得 4+1+1（3 枚），最优为 3+3（2 枚）。
算法本身选错，不是实现细节问题。
""",
    wrong="""
#include <iostream>
#include <vector>
#include <algorithm>
using namespace std;
int main() {
    int n, amount;
    cin >> n >> amount;
    vector<int> coins(n);
    for (int i = 0; i < n; i++) cin >> coins[i];
    sort(coins.rbegin(), coins.rend());
    int count = 0;
    int remain = amount;
    for (int c : coins) {
        while (remain >= c) {
            remain -= c;
            count++;
        }
    }
    if (remain != 0) cout << -1 << endl;
    else cout << count << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <vector>
#include <algorithm>
using namespace std;
int main() {
    int n, amount;
    cin >> n >> amount;
    vector<int> coins(n);
    for (int i = 0; i < n; i++) cin >> coins[i];
    const int INF = 1e9;
    vector<int> dp(amount + 1, INF);
    dp[0] = 0;
    for (int i = 1; i <= amount; i++) {
        for (int c : coins) {
            if (i - c >= 0 && dp[i - c] + 1 < dp[i]) dp[i] = dp[i - c] + 1;
        }
    }
    cout << (dp[amount] >= INF ? -1 : dp[amount]) << endl;
    return 0;
}
""",
    tests=[
        ("3 6\n1 3 4\n", "2\n"),
        ("3 11\n1 5 6\n", "2\n"),
        ("1 3\n2\n", "-1\n"),
    ],
)

# --------------------------------------------------------------------------
# 5. io_format_error — wrong delimiter between numbers
# --------------------------------------------------------------------------
add(
    id="s2-005-comma-separated-output",
    title="输出 1 到 n",
    statement="""
输入正整数 n（1 <= n <= 1000），输出 1 到 n，
数字之间用一个空格分隔。
""",
    tags=["输出格式", "基础语法"],
    difficulty="easy",
    root_cause="io_format_error",
    verdict="WA",
    notes="""
数字之间用了逗号分隔（`1,2,3`），而题目要求空格分隔。
注意：判题器会对输出做规范化（忽略行尾空格与多余空行），所以
行尾空格、多余换行这类差异不会被判 WA —— 只有分隔符、大小写、
数值内容这类实质差异才会。这也是本用例选逗号而非尾随空格的原因。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int n;
    cin >> n;
    for (int i = 1; i <= n; i++) {
        cout << i;
        if (i < n) cout << ",";
    }
    cout << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    int n;
    cin >> n;
    for (int i = 1; i <= n; i++) {
        if (i > 1) cout << " ";
        cout << i;
    }
    cout << endl;
    return 0;
}
""",
    tests=[("3\n", "1 2 3\n"), ("1\n", "1\n")],
)

# --------------------------------------------------------------------------
# 6. missing_case — handles positive but not negative / zero
# --------------------------------------------------------------------------
add(
    id="s2-006-sign-classify",
    title="判断数的正负",
    statement="""
输入一个整数 x，若 x > 0 输出 positive，x < 0 输出 negative，
x == 0 输出 zero。
""",
    tags=["条件分支"],
    difficulty="easy",
    root_cause="missing_case",
    verdict="WA",
    notes="""
只写了 x > 0 和 x < 0 两个分支，else 分支输出 "negative"，所以输入 0
时给出 negative。遗漏了 x == 0 这一必处理情况。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int x;
    cin >> x;
    if (x > 0) cout << "positive" << endl;
    else cout << "negative" << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    int x;
    cin >> x;
    if (x > 0) cout << "positive" << endl;
    else if (x < 0) cout << "negative" << endl;
    else cout << "zero" << endl;
    return 0;
}
""",
    tests=[("5\n", "positive\n"), ("0\n", "zero\n"), ("-3\n", "negative\n")],
)

# --------------------------------------------------------------------------
# 7. uninitialized_state — accumulator not reset per test / not initialized
# --------------------------------------------------------------------------
add(
    id="s2-007-max-without-init",
    title="数组最大值",
    statement="""
第一行输入 n（1 <= n <= 100），第二行输入 n 个整数（可为负数），
输出最大值。
""",
    tags=["数组", "初始化"],
    difficulty="easy",
    root_cause="uninitialized_state",
    verdict="WA",
    notes="""
maxVal 初始化为 0。当所有元素都是负数时，0 大于任何元素，
于是输出 0 而不是真正的最大值。这是"用 0 当初始值"的经典陷阱。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[105];
    for (int i = 0; i < n; i++) cin >> a[i];
    int maxVal = 0;
    for (int i = 0; i < n; i++) {
        if (a[i] > maxVal) maxVal = a[i];
    }
    cout << maxVal << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <climits>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[105];
    for (int i = 0; i < n; i++) cin >> a[i];
    int maxVal = INT_MIN;
    for (int i = 0; i < n; i++) {
        if (a[i] > maxVal) maxVal = a[i];
    }
    cout << maxVal << endl;
    return 0;
}
""",
    tests=[("3\n-1 -5 -3\n", "-1\n"), ("2\n1 2\n", "2\n"), ("1\n-7\n", "-7\n")],
)

# --------------------------------------------------------------------------
# 8. wrong_traversal — DFS used for shortest path
# --------------------------------------------------------------------------
add(
    id="s2-008-shortest-path-dfs",
    title="无权图最短路长度",
    statement="""
n 个点 m 条无向边（1 <= n <= 100），边权均为 1。
输入起点 s 和终点 t，输出 s 到 t 的最短距离，不可达输出 -1。
""",
    tags=["图论", "BFS", "DFS"],
    difficulty="hard",
    root_cause="wrong_traversal",
    verdict="WA",
    notes="""
用 DFS 返回"第一条到达目标的路径长度"是错的：DFS 沿第一条分支一路
深入到底，返回的只是碰巧先找到的那条路径，与最短无关。必须用 BFS
才能保证首次到达即最短。这里的关键是找到目标后**立即返回**（而不是
枚举所有路径后取最小值——那样虽然正确但会指数级超时）。
""",
    wrong="""
#include <iostream>
#include <vector>
using namespace std;
vector<int> adj[105];
bool visited[105];
int found = -1;
void dfs(int u, int dist, int target) {
    if (found != -1) return;
    if (u == target) {
        found = dist;
        return;
    }
    visited[u] = true;
    for (int v : adj[u]) {
        if (!visited[v]) dfs(v, dist + 1, target);
    }
    visited[u] = false;
}
int main() {
    int n, m;
    cin >> n >> m;
    for (int i = 0; i < m; i++) {
        int u, v;
        cin >> u >> v;
        adj[u].push_back(v);
        adj[v].push_back(u);
    }
    int s, t;
    cin >> s >> t;
    dfs(s, 0, t);
    cout << found << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <vector>
#include <queue>
using namespace std;
int main() {
    int n, m;
    cin >> n >> m;
    vector<vector<int>> adj(n + 1);
    for (int i = 0; i < m; i++) {
        int u, v;
        cin >> u >> v;
        adj[u].push_back(v);
        adj[v].push_back(u);
    }
    int s, t;
    cin >> s >> t;
    vector<int> dist(n + 1, -1);
    queue<int> q;
    dist[s] = 0;
    q.push(s);
    while (!q.empty()) {
        int u = q.front(); q.pop();
        for (int v : adj[u]) {
            if (dist[v] == -1) {
                dist[v] = dist[u] + 1;
                q.push(v);
            }
        }
    }
    cout << dist[t] << endl;
    return 0;
}
""",
    tests=[
        # 1 -> {2, 5}; the chain 2-3-4 hangs off 2 and DFS dives into it
        # first, reporting 4 hops for 1 -> 5 (real answer: 1).
        ("5 4\n1 2\n2 3\n3 4\n1 5\n1 5\n", "1\n"),
        # 1 -> {2, 4}; DFS walks 1-2-3-4 first (3 hops) before trying the
        # direct edge 1-4 (1 hop). Expected is the true shortest.
        ("4 4\n1 2\n2 3\n3 4\n1 4\n1 4\n", "1\n"),
        ("3 1\n1 2\n1 3\n", "-1\n"),
    ],
)

# --------------------------------------------------------------------------
# 9. wrong_data_type — integer division where real division is required
# --------------------------------------------------------------------------
add(
    id="s2-009-average-integer-division",
    title="平均数",
    statement="""
第一行输入 n，第二行输入 n 个整数，输出它们的平均数，
保留两位小数。
""",
    tags=["数据类型", "浮点运算"],
    difficulty="medium",
    root_cause="wrong_data_type",
    verdict="WA",
    notes="""
`sum / n` 两个操作数都是整数，执行整数除法后截断，再赋给 double
已经丢失小数部分。必须先把 sum 或 n 转成浮点。
""",
    wrong="""
#include <iostream>
#include <iomanip>
using namespace std;
int main() {
    int n;
    cin >> n;
    int x;
    long long sum = 0;
    for (int i = 0; i < n; i++) {
        cin >> x;
        sum += x;
    }
    double avg = sum / n;
    cout << fixed << setprecision(2) << avg << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <iomanip>
using namespace std;
int main() {
    int n;
    cin >> n;
    int x;
    long long sum = 0;
    for (int i = 0; i < n; i++) {
        cin >> x;
        sum += x;
    }
    double avg = (double)sum / n;
    cout << fixed << setprecision(2) << avg << endl;
    return 0;
}
""",
    tests=[("3\n1 2 2\n", "1.67\n"), ("2\n1 2\n", "1.50\n"), ("4\n1 1 1 2\n", "1.25\n")],
)

# --------------------------------------------------------------------------
# 10. misread_problem — computes sum of digits instead of count of digits
# --------------------------------------------------------------------------
add(
    id="s2-010-digit-count-vs-sum",
    title="数字的位数",
    statement="""
输入一个正整数 n（1 <= n <= 1e9），输出它的位数。
例如 1234 有 4 位。
""",
    tags=["基础语法", "题意理解"],
    difficulty="easy",
    root_cause="misread_problem",
    verdict="WA",
    notes="""
题目问的是"位数"，代码求的是"各位数字之和"。对 n=10 恰好都等于 1，
但对 1234 前者是 4、后者是 10。属于理解错题意。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    long long n;
    cin >> n;
    int s = 0;
    while (n > 0) {
        s += n % 10;
        n /= 10;
    }
    cout << s << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    long long n;
    cin >> n;
    int cnt = 0;
    while (n > 0) {
        cnt++;
        n /= 10;
    }
    cout << cnt << endl;
    return 0;
}
""",
    tests=[("1234\n", "4\n"), ("7\n", "1\n"), ("100000\n", "6\n")],
)

# --------------------------------------------------------------------------
# 11. wrong_complexity — O(n^2) where n is large → TLE
# --------------------------------------------------------------------------
add(
    id="s2-011-pair-sum-quadratic",
    title="是否存在两数之和等于目标（大数据）",
    statement="""
第一行输入 n 和目标值 target（1 <= n <= 200000），
第二行输入 n 个整数。若存在两个不同位置的数之和等于 target 输出 YES，
否则输出 NO。
""",
    tags=["复杂度分析", "哈希"],
    difficulty="medium",
    root_cause="wrong_complexity",
    verdict="TLE",
    notes="""
双重循环是 O(n^2)，n=2e5 时约 2e10 次操作，远超时限。
逻辑本身正确（暴力枚举两两组合），所以小用例 AC、大用例 TLE。
根因是复杂度而非正确性。
""",
    wrong="""
#include <iostream>
#include <vector>
using namespace std;
int main() {
    int n;
    long long target;
    cin >> n >> target;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    for (int i = 0; i < n; i++) {
        for (int j = i + 1; j < n; j++) {
            if (a[i] + a[j] == target) {
                cout << "YES" << endl;
                return 0;
            }
        }
    }
    cout << "NO" << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <vector>
#include <unordered_set>
using namespace std;
int main() {
    int n;
    long long target;
    cin >> n >> target;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    unordered_set<long long> seen;
    for (int i = 0; i < n; i++) {
        if (seen.count(target - a[i])) {
            cout << "YES" << endl;
            return 0;
        }
        seen.insert(a[i]);
    }
    cout << "NO" << endl;
    return 0;
}
""",
    tests=[
        ("4 9\n2 7 11 15\n", "YES\n"),
        ("3 10\n1 2 3\n", "NO\n"),
        # Large worst case: no pair sums to the target, so the O(n^2) loop
        # must run to completion (~2e10 comparisons). The reference
        # hash-set solution is O(n) and finishes immediately.
        ("200000 2000000000\n" + " ".join("1" for _ in range(200000)) + "\n", "NO\n"),
    ],
)

# --------------------------------------------------------------------------
# 12. off_by_one — binary search boundary
# --------------------------------------------------------------------------
add(
    id="s2-012-binary-search-boundary",
    title="二分查找目标值下标",
    statement="""
第一行输入 n 和 target，第二行输入 n 个严格递增的整数。
输出 target 的下标（0-based），不存在输出 -1。
""",
    tags=["二分查找", "循环边界"],
    difficulty="medium",
    root_cause="off_by_one",
    verdict="WA",
    notes="""
`hi = n - 1` 但循环条件写成 `while (lo < hi)`。当区间收缩到只剩一个
元素时循环提前退出，而该元素从未被检查：只要 target 落在最后一次
`lo == hi` 停留的位置上就会漏判。例如 `[1,3,5,7,9]` 查 9 时返回 -1。
""",
    wrong="""
#include <iostream>
#include <vector>
using namespace std;
int main() {
    int n;
    long long target;
    cin >> n >> target;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    int lo = 0, hi = n - 1, ans = -1;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (a[mid] == target) { ans = mid; break; }
        else if (a[mid] < target) lo = mid + 1;
        else hi = mid - 1;
    }
    cout << ans << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <vector>
using namespace std;
int main() {
    int n;
    long long target;
    cin >> n >> target;
    vector<long long> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    int lo = 0, hi = n - 1, ans = -1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (a[mid] == target) { ans = mid; break; }
        else if (a[mid] < target) lo = mid + 1;
        else hi = mid - 1;
    }
    cout << ans << endl;
    return 0;
}
""",
    tests=[
        ("5 9\n1 3 5 7 9\n", "4\n"),
        ("5 1\n1 3 5 7 9\n", "0\n"),
        ("4 5\n2 4 6 8\n", "-1\n"),
    ],
)

# --------------------------------------------------------------------------
# 13. boundary_condition — n == 1 special case in a max-pair problem
# --------------------------------------------------------------------------
add(
    id="s2-013-min-difference-single",
    title="相邻元素最小差",
    statement="""
第一行输入 n（1 <= n <= 1000），第二行输入 n 个整数。
输出排序后相邻元素之差的最小值。若 n == 1，输出 0。
""",
    tags=["排序", "边界条件"],
    difficulty="medium",
    root_cause="boundary_condition",
    verdict="WA",
    notes="""
没有处理 n == 1 的情况：循环从 i=1 开始，一次都不执行，
ans 保持初始值 INT_MAX 被直接输出。n >= 2 时逻辑正确。
""",
    wrong="""
#include <iostream>
#include <algorithm>
#include <climits>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[1005];
    for (int i = 0; i < n; i++) cin >> a[i];
    sort(a, a + n);
    int ans = INT_MAX;
    for (int i = 1; i < n; i++) {
        ans = min(ans, a[i] - a[i - 1]);
    }
    cout << ans << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <algorithm>
#include <climits>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[1005];
    for (int i = 0; i < n; i++) cin >> a[i];
    if (n == 1) { cout << 0 << endl; return 0; }
    sort(a, a + n);
    int ans = INT_MAX;
    for (int i = 1; i < n; i++) {
        ans = min(ans, a[i] - a[i - 1]);
    }
    cout << ans << endl;
    return 0;
}
""",
    tests=[("1\n42\n", "0\n"), ("3\n1 5 9\n", "4\n"), ("2\n10 3\n", "7\n")],
)

# --------------------------------------------------------------------------
# 14. no_output_on_edge — loop never runs, prints nothing
# --------------------------------------------------------------------------
add(
    id="s2-014-factorial-missing-output",
    title="阶乘尾随零的个数",
    statement="""
输入 n（0 <= n <= 100000），输出 n! 末尾 0 的个数。
约定 0! = 1。
""",
    tags=["数学", "边界条件"],
    difficulty="medium",
    root_cause="no_output_on_edge",
    verdict="WA",
    notes="""
用 `while (n > 0)` 累加 n/5，但输入 n == 0 时循环一次都不执行，
同时也没有兜底输出，程序不打印任何内容。对 n=0 应当输出 0。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    long long n;
    cin >> n;
    long long count = 0;
    while (n > 0) {
        count += n / 5;
        n /= 5;
    }
    if (n != 0) cout << count << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    long long n;
    cin >> n;
    long long count = 0;
    while (n > 0) {
        count += n / 5;
        n /= 5;
    }
    cout << count << endl;
    return 0;
}
""",
    tests=[("0\n", "0\n"), ("10\n", "2\n"), ("25\n", "6\n")],
)

# --------------------------------------------------------------------------
# 15. missing_case — fails to treat duplicate elements
# --------------------------------------------------------------------------
add(
    id="s2-015-unique-count-missing-duplicates",
    title="不同元素个数",
    statement="""
第一行输入 n（1 <= n <= 1000），第二行输入 n 个整数，
输出其中不同元素的个数。
""",
    tags=["数组", "去重"],
    difficulty="medium",
    root_cause="missing_case",
    verdict="WA",
    notes="""
排序后只比较 a[i] 与 a[0]（第一个元素），没有和"当前上一个不同值"
比较，所以当数组含多个不同值时统计错误。实际上是漏了重复元素这一
情况的正确处理方式。
""",
    wrong="""
#include <iostream>
#include <algorithm>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[1005];
    for (int i = 0; i < n; i++) cin >> a[i];
    sort(a, a + n);
    int distinct = 1;
    for (int i = 1; i < n; i++) {
        if (a[i] != a[0]) distinct++;
    }
    cout << distinct << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <algorithm>
using namespace std;
int main() {
    int n;
    cin >> n;
    int a[1005];
    for (int i = 0; i < n; i++) cin >> a[i];
    sort(a, a + n);
    int distinct = 1;
    for (int i = 1; i < n; i++) {
        if (a[i] != a[i - 1]) distinct++;
    }
    cout << distinct << endl;
    return 0;
}
""",
    tests=[("5\n1 1 2 2 3\n", "3\n"), ("3\n7 7 7\n", "1\n"), ("4\n1 2 3 4\n", "4\n")],
)

# --------------------------------------------------------------------------
# 16. uninitialized_state — DP table / visited not reset between queries
# --------------------------------------------------------------------------
add(
    id="s2-016-multi-query-not-reset",
    title="多次查询区间和",
    statement="""
第一行输入 n 和 q，第二行输入 n 个整数，
随后 q 行每行两个整数 l r，输出区间 [l, r] 的元素和（下标从 1 开始）。
""",
    tags=["前缀和", "状态重置"],
    difficulty="medium",
    root_cause="uninitialized_state",
    verdict="WA",
    notes="""
sum 在循环外声明且每次查询前没有清零，于是各次查询的结果不断累加。
第一次查询输出正确，从第二次起就错了——这是"状态未重置"的典型症状。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int n, q;
    cin >> n >> q;
    long long pre[100005];
    pre[0] = 0;
    for (int i = 1; i <= n; i++) {
        long long x;
        cin >> x;
        pre[i] = pre[i - 1] + x;
    }
    long long sum = 0;
    for (int i = 0; i < q; i++) {
        int l, r;
        cin >> l >> r;
        sum += pre[r] - pre[l - 1];
        cout << sum << endl;
    }
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    int n, q;
    cin >> n >> q;
    long long pre[100005];
    pre[0] = 0;
    for (int i = 1; i <= n; i++) {
        long long x;
        cin >> x;
        pre[i] = pre[i - 1] + x;
    }
    for (int i = 0; i < q; i++) {
        int l, r;
        cin >> l >> r;
        cout << pre[r] - pre[l - 1] << endl;
    }
    return 0;
}
""",
    tests=[
        ("5 3\n1 2 3 4 5\n1 2\n3 5\n1 5\n", "3\n12\n15\n"),
        ("3 2\n10 20 30\n1 3\n2 2\n", "60\n20\n"),
    ],
)

# --------------------------------------------------------------------------
# 17. wrong_algorithm — sliding window where prefix+suffix needed
# --------------------------------------------------------------------------
add(
    id="s2-017-max-subarray-kadane",
    title="最大子段和",
    statement="""
第一行输入 n（1 <= n <= 100000），第二行输入 n 个整数（可为负）。
输出连续子段的最大和（子段至少包含一个元素）。
""",
    tags=["动态规划", "Kadane"],
    difficulty="medium",
    root_cause="wrong_algorithm",
    verdict="WA",
    notes="""
代码丢弃了所有负数的贡献却仍要求子段非空——当数组全为负数时，
它返回的是 0（因为 ans 初始化为 0 且 cur 被重置为 0），
而正确答案是最大的那个负数。算法未覆盖"必须选一个"的约束。
""",
    wrong="""
#include <iostream>
#include <algorithm>
using namespace std;
int main() {
    int n;
    cin >> n;
    long long cur = 0, ans = 0;
    for (int i = 0; i < n; i++) {
        long long x;
        cin >> x;
        cur += x;
        if (cur < 0) cur = 0;
        ans = max(ans, cur);
    }
    cout << ans << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
#include <algorithm>
using namespace std;
int main() {
    int n;
    cin >> n;
    long long cur = 0, ans;
    for (int i = 0; i < n; i++) {
        long long x;
        cin >> x;
        if (i == 0) { cur = x; ans = x; continue; }
        cur = max(x, cur + x);
        ans = max(ans, cur);
    }
    cout << ans << endl;
    return 0;
}
""",
    tests=[
        ("4\n-1 -2 -3 -4\n", "-1\n"),
        ("5\n1 -2 3 4 -1\n", "7\n"),
        ("1\n-5\n", "-5\n"),
    ],
)

# --------------------------------------------------------------------------
# 18. io_format_error — wrong case / wrong separator
# --------------------------------------------------------------------------
add(
    id="s2-018-case-sensitive-yes-no",
    title="闰年判断",
    statement="""
输入年份 y（1 <= y <= 9999），若为闰年输出 "Yes"，否则输出 "No"。
闰年规则：能被 4 整除且不能被 100 整除，或能被 400 整除。
""",
    tags=["条件分支", "输出格式"],
    difficulty="easy",
    root_cause="io_format_error",
    verdict="WA",
    notes="""
判断逻辑完全正确，但输出用了小写 "yes"/"no"，而题目要求 "Yes"/"No"。
部分 OJ 对大小写敏感，这类错误常被误判为逻辑错误。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    int y;
    cin >> y;
    if ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0) cout << "yes" << endl;
    else cout << "no" << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    int y;
    cin >> y;
    if ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0) cout << "Yes" << endl;
    else cout << "No" << endl;
    return 0;
}
""",
    tests=[("2000\n", "Yes\n"), ("1900\n", "No\n"), ("2024\n", "Yes\n")],
)

# --------------------------------------------------------------------------
# 19. wrong_data_type — unsigned underflow on subtraction
# --------------------------------------------------------------------------
add(
    id="s2-019-unsigned-underflow",
    title="两数之差",
    statement="""
输入两个整数 a 和 b（-1e9 <= a, b <= 1e9），输出 a - b。
""",
    tags=["数据类型", "整数溢出"],
    difficulty="medium",
    root_cause="wrong_data_type",
    verdict="WA",
    notes="""
把 a、b 声明为 `unsigned int`，当 a < b 时 a - b 发生无符号下溢，
结果变成一个极大的正数。声明了错误的数据类型（无符号而非有符号）。
""",
    wrong="""
#include <iostream>
using namespace std;
int main() {
    unsigned int a, b;
    cin >> a >> b;
    cout << a - b << endl;
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
int main() {
    long long a, b;
    cin >> a >> b;
    cout << a - b << endl;
    return 0;
}
""",
    tests=[("10 3\n", "7\n"), ("3 10\n", "-7\n"), ("-5 -8\n", "3\n")],
)

# --------------------------------------------------------------------------
# 20. off_by_one — prefix/suffix array sized one too small
# --------------------------------------------------------------------------
add(
    id="s2-020-gcd-array-too-small",
    title="多组数据求最大公约数",
    statement="""
第一行输入 t（1 <= t <= 1000）表示数据组数。
每组一行输入两个正整数 a b（1 <= a, b <= 1e9），输出它们的最大公约数。
""",
    tags=["数学", "循环控制"],
    difficulty="medium",
    root_cause="off_by_one",
    verdict="WA",
    notes="""
读取 t 组数据的循环写成 `for (i = 0; i < t - 1; i++)`，少读了一组，
最后一组没有被处理，输出末尾缺少一行的结果。
""",
    wrong="""
#include <iostream>
using namespace std;
long long gcd(long long a, long long b) {
    while (b) { long long t = a % b; a = b; b = t; }
    return a;
}
int main() {
    int t;
    cin >> t;
    for (int i = 0; i < t - 1; i++) {
        long long a, b;
        cin >> a >> b;
        cout << gcd(a, b) << endl;
    }
    return 0;
}
""",
    correct="""
#include <iostream>
using namespace std;
long long gcd(long long a, long long b) {
    while (b) { long long t = a % b; a = b; b = t; }
    return a;
}
int main() {
    int t;
    cin >> t;
    for (int i = 0; i < t; i++) {
        long long a, b;
        cin >> a >> b;
        cout << gcd(a, b) << endl;
    }
    return 0;
}
""",
    tests=[("2\n12 18\n100 75\n", "6\n25\n"), ("1\n7 13\n", "1\n")],
)


# --------------------------------------------------------------------------
# Emit YAML
# --------------------------------------------------------------------------
def _yaml_escape_block(text: str, indent: str) -> str:
    """Render *text* as a YAML literal block scalar body."""
    lines = text.strip("\n").split("\n")
    return "\n".join(f"{indent}{line}" if line else "" for line in lines)


def render(case: dict) -> str:
    out: list[str] = []
    out.append(f"id: {case['id']}")
    out.append("title: " + str(case["title"]))
    out.append("statement: |")
    out.append(_yaml_escape_block(str(case["statement"]), "  "))
    out.append("knowledge_tags:")
    for tag in case["tags"]:
        out.append(f"  - {tag}")
    out.append(f"difficulty: {case['difficulty']}")
    out.append(f"expected_root_cause: {case['root_cause']}")
    out.append(f"observable_verdict: {case['verdict']}")
    out.append("notes: |")
    out.append(_yaml_escape_block(str(case["notes"]), "  "))
    out.append("wrong_code: |")
    out.append(_yaml_escape_block(str(case["wrong"]), "  "))
    out.append("correct_code: |")
    out.append(_yaml_escape_block(str(case["correct"]), "  "))
    out.append("test_cases:")
    for inp, expected in case["tests"]:
        out.append(f"  - input: {_yaml_scalar(inp)}")
        out.append(f"    expected_output: {_yaml_scalar(expected)}")
    return "\n".join(out) + "\n"


def _yaml_scalar(text: str) -> str:
    """Quote a scalar, escaping newlines as \\n so it stays one line."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n")
    return f'"{escaped}"'


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        path = OUT / f"{case['id']}.yaml"
        path.write_text(render(case), encoding="utf-8")
    print(f"wrote {len(CASES)} cases to {OUT}")


if __name__ == "__main__":
    main()
