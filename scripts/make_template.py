# -*- coding: utf-8 -*-
"""生成输入 Excel 模板 (多 working sheet = 多批次)

用法:
  python make_template.py --out <路径.xlsx> [--demo]
"""
import argparse
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HEADERS = [
    ("SKU编码", 14, True),
    ("品名", 22, False),
    ("长(mm)", 10, True),
    ("宽(mm)", 10, True),
    ("高(mm)", 10, True),
    ("箱数", 9, True),
    ("每箱件数", 10, False),
    ("单箱毛重(kg)", 13, False),
    ("边压强度ECT(kN/m)", 16, False),
    ("限堆层数", 10, False),
    ("备注", 26, False),
]

TITLE_F = Font(name="Microsoft YaHei", size=13, bold=True, color="FFFFFFFF")
HEAD_F = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFFFF")
BODY_F = Font(name="Microsoft YaHei", size=10)
NOTE_F = Font(name="Microsoft YaHei", size=9, color="7F7F7F")
FILL_TITLE = PatternFill("solid", fgColor="FF2F5597")
FILL_HEAD = PatternFill("solid", fgColor="FF4472C4")
FILL_REQ = PatternFill("solid", fgColor="FFD9E2F3")
FILL_OPT = PatternFill("solid", fgColor="FFF2F2F2")
FILL_SAMPLE = PatternFill("solid", fgColor="FFFFF2CC")
thin = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def build_sheet(ws, title, sample_rows=None):
    """写一个批次的表头与说明。"""
    ws["A1"] = f"批次: {title}"
    ws["A1"].font = TITLE_F
    ws["A1"].fill = FILL_TITLE
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEADERS))
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 26

    # 第2行 = 表头
    for j, (h, w, req) in enumerate(HEADERS, 1):
        c = ws.cell(2, j, h)
        c.font = HEAD_F
        c.fill = FILL_REQ if req else FILL_OPT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.row_dimensions[2].height = 30

    rows = sample_rows or []
    for i, r in enumerate(rows, 3):
        for j, v in enumerate(r, 1):
            c = ws.cell(i, j, v)
            c.font = BODY_F
            c.fill = FILL_SAMPLE
            c.border = BORDER
            c.alignment = Alignment(horizontal="center", vertical="center")

    note_row = 3 + len(rows) + 1
    notes = [
        "填写说明:",
        "1. 每个 working sheet = 一个批次 (同一批次一起发运)。需要分批请复制本 sheet。",
        "2. 必填列 (浅蓝): SKU编码 / 长(mm) / 宽(mm) / 高(mm) / 箱数 —— 尺寸必须精确, 单位毫米。",
        "3. 可选列 (浅灰): 品名 / 每箱件数 / 单箱毛重 / 边压强度ECT / 限堆层数 / 备注。",
        "4. 单箱毛重: 填了才能做承重与重心校验 (强烈建议填)。",
        "5. 边压强度ECT: 常见三层B类纸箱 5.0~7.5 kN/m; 缺省按 6.0 处理。",
        "6. 限堆层数: 不填则由凯利卡特公式 (ECT + 湿度/海运折减 + 安全系数3) 自动推算。",
        "7. 尺寸单位统一 mm, 不要混用 cm —— 解析器不做单位换算, 填错会导致静默的尺寸错误。",
        "8. 删除示例行后再填自己的数据; 表头文字不要改 (解析器按关键词识别列)。",
    ]
    for k, t in enumerate(notes):
        c = ws.cell(note_row + k, 1, t)
        c.font = NOTE_F
        c.alignment = Alignment(horizontal="left", vertical="center")
    ws.freeze_panes = "A3"


# 演示数据 —— 全部为虚构商品, 刻意与任何真实业务数据无关。
# (编码, 品名, 长mm, 宽mm, 高mm, 箱数, 每箱件数, 单箱毛重kg, ECT kN/m, 限堆层数, 备注)
DEMO_B1 = [
    ["SKU-A001", "演示商品 甲", 400, 300, 250, 1200, 4, 12.0, 6.0, "", "示例行, 请删除"],
    ["SKU-A002", "演示商品 乙", 500, 400, 350, 320, 6, 16.0, 6.0, "", ""],
    ["SKU-A003", "演示商品 丙", 450, 350, 280, 180, 8, 14.0, 5.5, "", ""],
]
DEMO_B2 = [
    ["SKU-B001", "演示商品 丁", 540, 400, 270, 110, 9, 12.0, 7.0, "", "第二批次示例"],
    ["SKU-B002", "演示商品 戊", 380, 300, 320, 150, 6, 16.0, 6.0, "", ""],
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--demo", action="store_true", help="带示例数据")
    args = ap.parse_args()
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "批次1"
    build_sheet(ws1, "批次1", DEMO_B1 if args.demo else [])
    ws2 = wb.create_sheet("批次2")
    build_sheet(ws2, "批次2 (示例: 可删除)", DEMO_B2 if args.demo else [])
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    wb.save(args.out)
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
