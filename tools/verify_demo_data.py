"""Verify the demo dataset renders end to end, over the real HTTP API.

Run this after ``tools/seed_demo_data.py`` and with the sidecar up: it logs
in as the demo teacher and a demo student, then calls every read endpoint
the demo touches and prints what each one actually returned. The point is
to catch the failure mode where seeding *succeeded* but a page is still
blank because an endpoint reads a table the seed did not populate.

Usage::

    python tools/verify_demo_data.py            # needs the sidecar on :8123
    python tools/verify_demo_data.py --url http://127.0.0.1:8123 --token dev-local-token
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

TEACHER = ("demo_teacher", "demo1234")
STUDENT = ("demo_s05", "demo1234")


def _call(base: str, sidecar_token: str, path: str, *, auth: str | None = None,
          method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("X-Sidecar-Token", sidecar_token)
    if auth:
        req.add_header("Authorization", f"Bearer {auth}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=20) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8123")
    ap.add_argument("--token", default="dev-local-token")
    args = ap.parse_args()
    base, tok = args.url, args.token

    def login(cred):
        return _call(base, tok, "/api/v1/auth/login", method="POST",
                     body={"username": cred[0], "password": cred[1]})["token"]

    try:
        t = login(TEACHER)
        s = login(STUDENT)
    except urllib.error.HTTPError as e:
        print(f"登录失败（HTTP {e.code}）：先跑 tools/seed_demo_data.py --write")
        return 1

    rows: list[tuple[str, str]] = []

    ov = _call(base, tok, "/api/v1/teacher/overview", auth=t)
    rows.append((
        "教师总览",
        f"学生 {ov['students']} · 提交 {ov['submissions']} · 通过 {ov['accepted']} · "
        f"正确率 {ov['accuracy']:.1%} · 待处理标记 {ov['open_flags']}",
    ))
    ks = _call(base, tok, "/api/v1/teacher/knowledge-stats", auth=t)
    rows.append(("知识点统计", f"{len(ks)} 个知识点，最高 {ks[0]['tag']} {ks[0]['accuracy']:.0%}" if ks else "空"))
    ed = _call(base, tok, "/api/v1/teacher/error-distribution", auth=t)
    covered = len([r for r in ed if r["label"]])
    rows.append(("错因分布", f"{covered}/12 类 · 最多 «{ed[0]['label']}» {ed[0]['count']} 次"))
    st = _call(base, tok, "/api/v1/teacher/students", auth=t)
    rows.append(("学生名册", f"{len(st)} 人（{len([x for x in st if x['submissions']])} 人有提交）"))
    al = _call(base, tok, "/api/v1/teacher/alerts", auth=t)
    kinds: dict[str, int] = {}
    for x in al:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    rows.append(("告警", f"{len(al)} 条 · " + " ".join(f"{k}×{v}" for k, v in kinds.items())))
    fl = _call(base, tok, "/api/v1/teacher/submissions/flagged", auth=t)["items"]
    has_ev = sum(1 for x in fl if x.get("process") and x["process"].get("reasons"))
    rows.append(("过程标记队列", f"{len(fl)} 条（{has_ev} 条带证据文字）"))
    asg = _call(base, tok, "/api/v1/teacher/assignments", auth=t)
    rows.append(("作业", f"{len(asg)} 个"))
    cls = _call(base, tok, "/api/v1/teacher/classrooms", auth=t)
    mem = _call(base, tok, f"/api/v1/teacher/classrooms/{cls[0]['id']}/members", auth=t) if cls else []
    rows.append(("班级", f"{len(cls)} 个 · 首个班 {len(mem)} 人"))
    if asg:
        a = _call(base, tok, f"/api/v1/teacher/assignments/{asg[0]['id']}/stats", auth=t)
        rows.append(("作业统计", f"作业#{asg[0]['id']} 覆盖 {len(a['problems'])} 题"))
    rad = _call(base, tok, "/api/v1/profile/radar", auth=s)
    rows.append(("学生雷达图", f"{len(rad['indicators'])} 维 · 非零 {sum(1 for v in rad['values'] if v > 0)}"))
    mb = _call(base, tok, "/api/v1/mistake-book", auth=s)
    rows.append(("学生错题本", f"{len(mb)} 条 · 含分段建议 " + ("是" if mb and mb[0].get("hint") else "否")))
    due = _call(base, tok, "/api/v1/review/due", auth=s)
    rows.append(("待复习", f"{len(due)} 条（SM-2）"))
    mine = _call(base, tok, "/api/v1/my/assignments", auth=s)
    rows.append(("我的作业", f"{len(mine)} 个"))

    w = max(len(n) for n, _ in rows)
    print("=" * (w + 56))
    print("  演示数据端到端验证")
    print("=" * (w + 56))
    for name, detail in rows:
        print(f"  OK  {name:<{w}}  {detail}")
    print("=" * (w + 56))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
