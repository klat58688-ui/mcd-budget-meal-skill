# -*- coding: utf-8 -*-
"""
生成 README 用的仿真终端演示 GIF（docs/demo.gif）。

无真实 MCP Token 时的等效方案：按 demo.py 真实输出格式逐帧绘制终端动画，
拿到 Token 实录后可随时替换同名文件。

运行：python scripts/make_demo_gif.py
依赖：Pillow
"""

from PIL import Image, ImageDraw, ImageFont

W, H = 920, 620
BG = (24, 26, 32)          # 终端深色底
FG = (214, 217, 224)       # 普通输出
GREEN = (80, 200, 120)     # 提示符/成功
YELLOW = (230, 200, 90)    # 高亮金额
RED = (235, 100, 100)      # 立省金额（A股红涨，省钱也红）
CYAN = (110, 190, 235)     # 步骤标题
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT = ImageFont.truetype(FONT_PATH, 17)
FONT_B = ImageFont.truetype(FONT_PATH, 19)
LINE_H = 26
PAD = 18

# 演示剧本：(文本, 颜色, 行内高亮片段)
# 高亮用 {"¥19.00": RED} 形式按子串着色
SCRIPT = [
    ("$ python demo.py", GREEN, {}),
    ("== 麦门穷鬼套餐生成器 Demo ==  预算 ¥20 / 2 人 / 城市 广州", FG, {"¥20": YELLOW}),
    ("", FG, {}),
    ("[1/5] list_tools 返回 7 个工具", CYAN, {}),
    ("[2/5] 已自动领取全部可领优惠券", CYAN, {}),
    ("      满30减8券、麦乐鸡立减5券、全场立减2券", FG, {}),
    ("[3/5] 菜单获取成功，在售餐品 42 个", CYAN, {}),
    ("[4/5] 凑单完成，Top3（按最便宜）：", CYAN, {}),
    ("  方案① ¥19.00（原价 ¥36，立省 ¥17）麦香鸡×2 + 小薯条 + 中杯可乐×2",
     FG, {"¥19.00": YELLOW, "立省 ¥17": RED}),
    ("  方案② ¥20.00（原价 ¥35，立省 ¥15）麦香鸡×2 + 麦乐鸡5块",
     FG, {"¥20.00": YELLOW, "立省 ¥15": RED}),
    ("  方案③ ¥18.50（原价 ¥33，立省 ¥14.5）麦香鸡×1 + 小薯条×2 + 中杯可乐",
     FG, {"¥18.50": YELLOW, "立省 ¥14.5": RED}),
    ("  吃得最饱 Top1：饱腹感 7.5，¥20.00", FG, {}),
    ("[5/5] 用户选择方案①，下单成功！订单号 M20261009123456（自取）", GREEN, {}),
    ("", FG, {}),
    ("---- 省钱战报 ----", CYAN, {}),
    ("🍔 麦门穷鬼套餐战报".replace("🍔 ", ""), FG, {}),
    ("本单 2 人餐：原价 ¥36.00，券后 ¥19.00，立省 ¥17.00！",
     FG, {"¥36.00": FG, "¥19.00": YELLOW, "立省 ¥17.00": RED}),
    ("用券：「满30减8券」", FG, {}),
    ("配置：麦香鸡×2 + 小薯条×1 + 中杯可乐×2", FG, {}),
    ("#麦门穷鬼套餐 #麦当劳MCP", CYAN, {}),
]


def draw_highlighted(draw, xy, text, base_color, highlights):
    """逐段绘制，对 highlights 中的子串换色（只处理首个命中）"""
    x, y = xy
    i = 0
    while i < len(text):
        hit = None
        for kw in highlights:
            if text.startswith(kw, i):
                hit = kw
                break
        if hit:
            draw.text((x, y), hit, font=FONT, fill=highlights[hit])
            x += draw.textlength(hit, font=FONT)
            i += len(hit)
        else:
            ch = text[i]
            draw.text((x, y), ch, font=FONT, fill=base_color)
            x += draw.textlength(ch, font=FONT)
            i += 1


def render_frame(lines, typing_line=None, typing_chars=0):
    """绘制一帧：已完整显示的行 + 正在打字的行（前 typing_chars 个字符）"""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # 终端窗口顶栏
    d.rectangle([0, 0, W, 34], fill=(36, 39, 46))
    for j, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([14 + j * 22, 12, 26 + j * 22, 24], fill=c)
    d.text((W // 2 - 60, 8), "demo — bash", font=FONT, fill=(150, 154, 162))

    y = 34 + PAD
    for text, color, hl in lines:
        if text:
            draw_highlighted(d, (PAD, y), text, color, hl)
        y += LINE_H
    if typing_line is not None:
        text, color, hl = typing_line
        shown = text[:typing_chars]
        if shown:
            draw_highlighted(d, (PAD, y), shown, color, hl)
        # 光标
        cx = PAD + (d.textlength(shown, font=FONT) if shown else 0)
        d.rectangle([cx, y + 2, cx + 9, y + 22], fill=FG)
    return img


def main():
    frames = []
    durations = []

    # 第一行命令：逐字打字效果
    cmd = SCRIPT[0]
    for n in range(1, len(cmd[0]) + 1, 2):
        frames.append(render_frame([], typing_line=cmd, typing_chars=n))
        durations.append(60)
    frames.append(render_frame([], typing_line=cmd, typing_chars=len(cmd[0])))
    durations.append(400)

    # 后续行：逐行出现，关键行停顿稍长
    shown = [cmd]
    for line in SCRIPT[1:]:
        frames.append(render_frame(shown, typing_line=None))
        durations.append(90)
        shown.append(line)
        frames.append(render_frame(shown))
        durations.append(420 if line[0].startswith(("  方案", "本单", "[5/5]")) else 200)

    # 结尾定格 3 秒
    frames.append(render_frame(shown))
    durations.append(3000)

    out = "docs/demo.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)
    print(f"生成完成：{out}，共 {len(frames)} 帧")


if __name__ == "__main__":
    main()
