"""手绘风格「校园科技日」任务分解结构图（WBS）生成脚本。
用 Pillow 绘制，中文使用系统楷体 simkai.ttf，线条做手绘抖动处理。
"""
import math
import random

from PIL import Image, ImageDraw, ImageFont

random.seed(20261008)

SS = 2                       # 超采样倍数（输出 = 逻辑坐标 * SS）
W, H = 1400, 1000            # 逻辑画布尺寸
FONT_PATH = r"C:\Windows\Fonts\simkai.ttf"

PAPER = (248, 244, 234)
GRID = (228, 234, 241)
INK = (46, 58, 80)
SOFT = (124, 138, 160)
FAINT = (138, 151, 171)
BOXBG = (255, 255, 255)

LW = 2.4                     # 逻辑线宽


def S(v):
    return v * SS


img = Image.new("RGB", (int(S(W)), int(S(H))), PAPER)
d = ImageDraw.Draw(img)

# ---- 方格纸底纹 ----
step = int(S(34))
for x in range(0, int(S(W)) + 1, step):
    d.line([(x, 0), (x, int(S(H)))], fill=GRID, width=1)
for y in range(0, int(S(H)) + 1, step):
    d.line([(0, y), (int(S(W)), y)], fill=GRID, width=1)


def jitter(pts, amp):
    """对点序列做平滑随机扰动，得到手绘感。"""
    n = len(pts)
    raw = [(random.uniform(-amp, amp), random.uniform(-amp, amp)) for _ in range(n)]
    out = []
    for i in range(n):
        sx = sy = 0.0
        for k in (-2, -1, 0, 1, 2):
            j = (i + k) % n
            sx += raw[j][0]
            sy += raw[j][1]
        out.append((pts[i][0] + sx / 5.0, pts[i][1] + sy / 5.0))
    return out


def px(pts):
    return [(S(x), S(y)) for x, y in pts]


def hand_line(p1, p2, amp=1.8, w=LW):
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    n = max(2, int(dist / 26))
    base = [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]
    d.line(px(jitter(base, amp)), fill=INK, width=int(round(w * SS)), joint="curve")


def rrect_pts(x, y, w, h, r, rot=0.0):
    pts = []

    def arc(cx, cy, a0, a1):
        a = a0
        while a <= a1:
            pts.append((cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))))
            a += 10

    pts.append((x + r, y))
    pts.append((x + w - r, y))
    arc(x + w - r, y + r, -90, 0)
    pts.append((x + w, y + h - r))
    arc(x + w - r, y + h - r, 0, 90)
    pts.append((x + r, y + h))
    arc(x + r, y + h - r, 90, 180)
    pts.append((x, y + r))
    arc(x + r, y + r, 180, 270)
    pts.append((x + r, y))

    if rot:
        cx, cy = x + w / 2, y + h / 2
        ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        pts = [(cx + (p[0] - cx) * ca - (p[1] - cy) * sa,
                cy + (p[0] - cx) * sa + (p[1] - cy) * ca) for p in pts]
    return pts


def draw_box(x, y, w, h, r=8):
    rot = random.uniform(-0.9, 0.9)
    base = rrect_pts(x, y, w, h, r, rot)
    d.polygon(px(jitter(base, 1.9)), fill=BOXBG)
    for _ in range(2):                       # 描两遍，像铅笔反复勾
        d.line(px(jitter(base, 1.9)), fill=INK, width=int(round(LW * SS)), joint="curve")


# ---- 版面坐标（逻辑坐标）----
ROOT = (232, 533, 150, 64)
PHASES = [(482, 290, 170, 54), (482, 476, 170, 54), (482, 631, 170, 54), (482, 786, 170, 54)]
SUBS = [(747, 233 + 62 * i, 420, 44) for i in range(11)]

PHASE_Y = [317, 503, 658, 813]
SUB_Y = [255 + 62 * i for i in range(11)]

# ---- 连线 ----
hand_line((382, 565), (422, 565))
hand_line((422, 317), (422, 813))
for y in PHASE_Y:
    hand_line((422, y), (482, y))

for pidx, (p0, p1) in enumerate([(0, 2), (3, 5), (6, 7), (8, 10)]):
    py = PHASE_Y[pidx]
    hand_line((652, py), (697, py))
    hand_line((697, SUB_Y[p0]), (697, SUB_Y[p1]))
    for i in range(p0, p1 + 1):
        hand_line((697, SUB_Y[i]), (747, SUB_Y[i]))

# ---- 方框 ----
draw_box(*ROOT, r=9)
for b in PHASES:
    draw_box(*b, r=9)
for b in SUBS:
    draw_box(*b, r=8)

# ---- 文字 ----
f_title = ImageFont.truetype(FONT_PATH, int(38 * SS))
f_root = ImageFont.truetype(FONT_PATH, int(24 * SS))
f_note = ImageFont.truetype(FONT_PATH, int(15 * SS))
f_phase = ImageFont.truetype(FONT_PATH, int(23 * SS))
f_sub = ImageFont.truetype(FONT_PATH, int(20 * SS))
f_foot = ImageFont.truetype(FONT_PATH, int(18 * SS))

title = "校园科技日 · 任务分解结构图（WBS）"
d.text((S(700), S(78)), title, font=f_title, fill=INK, anchor="mm")
tw = d.textlength(title, font=f_title)
hand_line(((S(700) - tw / 2) / SS, 102), ((S(700) + tw / 2) / SS, 106), amp=2.2, w=2.6)

d.text((S(307), S(557)), "校园科技日", font=f_root, fill=INK, anchor="mm")
d.text((S(307), S(585)), "（总目标）", font=f_note, fill=SOFT, anchor="mm")

for (bx, by, bw, bh), y, txt in zip(PHASES, PHASE_Y, ["1　前期策划", "2　资源协调", "3　宣传推广", "4　现场执行"]):
    d.text((S(502), S(y)), txt, font=f_phase, fill=INK, anchor="lm")

SUB_TXT = [
    "1.1　确定活动主题与形式", "1.2　制定活动方案与预算", "1.3　排定时间进度表",
    "2.1　场地申请与布置", "2.2　设备与物资采购", "2.3　人员招募与分工",
    "3.1　海报与推文设计", "3.2　线上报名与通知",
    "4.1　布展与设备调试", "4.2　主持与流程把控", "4.3　应急处理与收尾",
]
for y, txt in zip(SUB_Y, SUB_TXT):
    d.text((S(767), S(y)), txt, font=f_sub, fill=INK, anchor="lm")

d.text((S(232), S(960)), "分解方式：按「阶段（一级）→ 具体任务（二级）」逐层拆解",
       font=f_foot, fill=FAINT, anchor="lm")

img.save(r"D:\BaiduNetdiskDownload\AlgoForge\wbs_campus_tech_day.png")
print("saved", img.size)
