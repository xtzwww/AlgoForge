"""手绘风格「新生报到流程」AND-OR 图生成脚本。
与 draw_wbs.py 同一套手绘风格：楷体 + 铅笔抖动线条 + 方格纸底纹。
"""
import math
import random

from PIL import Image, ImageDraw, ImageFont

random.seed(20261009)

SS = 2
W, H = 1400, 820
FONT_PATH = r"C:\Windows\Fonts\simkai.ttf"

PAPER = (248, 244, 234)
GRID = (228, 234, 241)
INK = (46, 58, 80)
SOFT = (124, 138, 160)
FAINT = (138, 151, 171)
BOXBG = (255, 255, 255)
TINT = (238, 243, 250)        # AND / OR 节点底色，稍作区分

LW = 2.4


def S(v):
    return v * SS


img = Image.new("RGB", (int(S(W)), int(S(H))), PAPER)
d = ImageDraw.Draw(img)

step = int(S(34))
for x in range(0, int(S(W)) + 1, step):
    d.line([(x, 0), (x, int(S(H)))], fill=GRID, width=1)
for y in range(0, int(S(H)) + 1, step):
    d.line([(0, y), (int(S(W)), y)], fill=GRID, width=1)


def jitter(pts, amp):
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


def draw_box(x, y, w, h, r=8, fill=BOXBG):
    rot = random.uniform(-0.9, 0.9)
    base = rrect_pts(x, y, w, h, r, rot)
    d.polygon(px(jitter(base, 1.9)), fill=fill)
    for _ in range(2):
        d.line(px(jitter(base, 1.9)), fill=INK, width=int(round(LW * SS)), joint="curve")


f_title = ImageFont.truetype(FONT_PATH, int(38 * SS))
f_root = ImageFont.truetype(FONT_PATH, int(24 * SS))
f_task = ImageFont.truetype(FONT_PATH, int(22 * SS))
f_op = ImageFont.truetype(FONT_PATH, int(17 * SS))
f_note = ImageFont.truetype(FONT_PATH, int(17 * SS))
f_leg = ImageFont.truetype(FONT_PATH, int(18 * SS))

# ---- 版面 ----
ROOT = (580, 133, 240, 64)
AND = (662, 240, 76, 36)
TASK_Y, TASK_H, TASK_W = 350, 60, 210
TASK_CX = [180, 440, 700, 960, 1220]
OR = (662, 462, 76, 36)
L3_Y, L3_H, L3_W = 560, 60, 200
L3_CX = [560, 840]

# ---- 连线 ----
hand_line((700, 197), (700, 240))                     # 根 → AND
hand_line((700, 276), (700, 310))                     # AND → 分配横线
hand_line((TASK_CX[0], 310), (TASK_CX[-1], 310))
for cx in TASK_CX:
    hand_line((cx, 310), (cx, TASK_Y))

hand_line((700, 410), (700, 462))                     # 缴费确认 → OR
hand_line((700, 498), (700, 530))
hand_line((L3_CX[0], 530), (L3_CX[-1], 530))
for cx in L3_CX:
    hand_line((cx, 530), (cx, L3_Y))

# ---- 方框 ----
draw_box(*ROOT, r=10)
draw_box(*AND, r=7, fill=TINT)
for cx in TASK_CX:
    draw_box(cx - TASK_W / 2, TASK_Y, TASK_W, TASK_H, r=9)
draw_box(*OR, r=7, fill=TINT)
for cx in L3_CX:
    draw_box(cx - L3_W / 2, L3_Y, L3_W, L3_H, r=9)

# ---- 文字 ----
title = "新生报到流程 · AND-OR 图"
d.text((S(700), S(66)), title, font=f_title, fill=INK, anchor="mm")
tw = d.textlength(title, font=f_title)
hand_line(((S(700) - tw / 2) / SS, 92), ((S(700) + tw / 2) / SS, 96), amp=2.2, w=2.6)

d.text((S(700), S(165)), "完成新生报到", font=f_root, fill=INK, anchor="mm")
d.text((S(700), S(258)), "AND", font=f_op, fill=INK, anchor="mm")

for cx, txt in zip(TASK_CX, ["身份核验", "宿舍安排", "缴费确认", "校园卡领取", "入学材料提交"]):
    d.text((S(cx), S(TASK_Y + TASK_H / 2)), txt, font=f_task, fill=INK, anchor="mm")

d.text((S(700), S(480)), "OR", font=f_op, fill=INK, anchor="mm")
d.text((S(756), S(480)), "二选一", font=f_note, fill=SOFT, anchor="lm")

for cx, txt in zip(L3_CX, ["线上缴费", "现场窗口缴费"]):
    d.text((S(cx), S(L3_Y + L3_H / 2)), txt, font=f_task, fill=INK, anchor="mm")

# ---- 图例 ----
draw_box(180, 682, 76, 36, r=7, fill=TINT)
d.text((S(218), S(700)), "AND", font=f_op, fill=INK, anchor="mm")
d.text((S(276), S(700)), "节点：其下所有子任务都完成后，父任务才算完成", font=f_leg, fill=FAINT, anchor="lm")

draw_box(180, 740, 76, 36, r=7, fill=TINT)
d.text((S(218), S(758)), "OR", font=f_op, fill=INK, anchor="mm")
d.text((S(276), S(758)), "节点：其下任一子任务完成，父任务即可完成", font=f_leg, fill=FAINT, anchor="lm")

img.save(r"D:\BaiduNetdiskDownload\AlgoForge\andor_freshman_checkin.png")
print("saved", img.size)
