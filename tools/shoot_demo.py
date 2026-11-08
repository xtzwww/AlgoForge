"""Capture demo screenshots of the running web build for the graduation defense.

Prereqs (all on 127.0.0.1): static build 5179, sidecar 8123, postgres 5432,
demo data seeded via tools/seed_demo_data.py.

Run:  python tools/shoot_demo.py
Out:  毕业论文/演示截图/*.png

UI facts this script depends on (learned by DOM probing):
  * "登录" in the top bar is a plain text node, not a <button>.
    Clicking it routes to #/login and reveals a Naive UI form.
  * The login form has exactly two <input>: [0]=text username, [1]=password,
    plus a submit <button> whose innerText is "登录".
  * The teacher console also shows a 学生/老师 tab pair; teacher is default.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "毕业论文" / "演示截图"
BASE = "http://127.0.0.1:5179"
TEACHER = "demo_teacher"
STUDENT = "demo_s01"
PASSWORD = "demo1234"
VIEWPORT = {"width": 1600, "height": 1000}
CHROME = r"C:/Users/雪团子/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"


def _settle(page, ms: int = 2200) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001 - busy pollers can keep the net never-idle
        pass
    page.wait_for_timeout(ms)


def _login(page, url: str, username: str) -> bool:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # open the login view (top-bar "登录" is a text node, not a button)
    try:
        page.get_by_text("登录", exact=True).first.click(timeout=6000)
    except Exception:
        page.evaluate(
            """() => {
                for (const e of document.querySelectorAll('*')) {
                    if (!e.children.length && (e.textContent||'').trim()==='登录') { e.click(); return; }
                }
            }"""
        )
    page.wait_for_timeout(1500)

    inputs = page.locator("input")
    if inputs.count() < 2:
        print(f"  [fail] login form not found for {username}")
        return False
    inputs.nth(0).fill(username)
    inputs.nth(1).fill(PASSWORD)
    page.locator("button[type=submit]").first.click(timeout=6000)
    _settle(page, 3200)
    return True


def _click_nav(page, label: str, fname: str, shots: list, note: str) -> None:
    try:
        item = page.get_by_text(label, exact=True).first
        if item.count() == 0:
            print(f"  [skip] nav not found: {label}")
            return
        item.click(timeout=8000)
        _settle(page, 2600)
        page.screenshot(path=str(OUT / fname))
        shots.append((fname, note))
    except Exception as exc:  # noqa: BLE001
        print(f"  [fail] nav '{label}': {type(exc).__name__} {str(exc)[:110]}")


TEACHER_NAV = [
    ("教学概览", "10_教师端_教学概览.png", "教师端 · 教学概览"),
    ("学情分析", "11_教师端_学情分析.png", "教师端 · 学情分析"),
    ("作业", "12_教师端_作业.png", "教师端 · 作业"),
    ("存疑提交", "13_教师端_存疑提交.png", "教师端 · 存疑提交"),
    ("题库", "14_教师端_题库.png", "教师端 · 题库"),
    ("名册与班级", "15_教师端_名单与班级.png", "教师端 · 名册与班级"),
]
# verified nav labels on the student console (SPAN.nav-label)
STUDENT_NAV = [
    ("我的作业", "20_学生端_我的作业.png", "学生端 · 我的作业"),
    ("题库", "21_学生端_题库.png", "学生端 · 题库（自由练习）"),
    ("错题本", "22_学生端_错题本.png", "学生端 · 错题本"),
    ("画像", "23_学生端_学情画像.png", "学生端 · 学情画像"),
    ("复习", "24_学生端_复习计划.png", "学生端 · 复习计划"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    shots: list[tuple[str, str]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=CHROME,
            args=["--no-proxy-server", "--disable-gpu"],
        )
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        ctx.set_default_timeout(30000)

        page = ctx.new_page()
        if _login(page, f"{BASE}/teacher.web.html", TEACHER):
            page.screenshot(path=str(OUT / "10_教师端_教学概览.png"))
            shots.append(("10_教师端_教学概览.png", "教师端 · 教学概览"))
            for label, fname, note in TEACHER_NAV[1:]:
                _click_nav(page, label, fname, shots, note)
        page.close()

        page = ctx.new_page()
        if _login(page, f"{BASE}/index.web.html", STUDENT):
            for label, fname, note in STUDENT_NAV:
                _click_nav(page, label, fname, shots, note)
        page.close()

        ctx.close()
        browser.close()

    print("\n" + "=" * 64)
    for name, note in shots:
        p = OUT / name
        ok = p.exists() and p.stat().st_size > 3000
        size = p.stat().st_size if p.exists() else 0
        print(f"{'OK ' if ok else 'BAD'} {name:<36} {size:>8} B  {note}")
    print("=" * 64)
    print("OUT:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
