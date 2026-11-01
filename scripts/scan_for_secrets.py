# -*- coding: utf-8 -*-
"""发布前数据泄漏扫描 —— 检查待发布目录里是否残留商业数据 / 凭据 / 本机路径。

用法:
    python scripts/scan_for_secrets.py <待发布目录> [--json]
        [--sku-blacklist <文件>] [--words-blacklist <文件>]

检查项:
  1. 真实业务 SKU 编码 (通用形态匹配, 精确黑名单需 --sku-blacklist)
  2. 真实商品品名 (需 --words-blacklist 提供)
  3. 运行期产物目录 (ui/workspace/ 等, 可能含订单数据)
  4. 本机绝对路径 (含用户名)
  5. 凭据 / 密钥 / token
  6. 二进制文件里的可疑字符串 (xlsx / zip 内的文本)

注意: 本脚本自身**不得**包含任何真实业务数据 —— 检测用的 SKU / 品名黑名单
必须由外部文件传入, 否则扫描器本身就成了泄露源。

退出码: 0 = 无高危项, 可发布; 1 = 发现高危项
"""
import argparse
import json
import os
import re
import sys
import zipfile

# ---- 真实业务数据特征 ----
# 注意: 本脚本自身不得包含任何真实业务数据 (否则扫描器就是泄露源)。
# 因此只保留**通用形态**的检测规则; 具体的 SKU / 品名黑名单由外部传入:
#     python scan_for_secrets.py <dir> --sku-blacklist sku.txt --words-blacklist words.txt
#
# 通用规则: 形如 XX-1234 / XX-1234ABC 的商品编码 (短横线 + 数字 + 可选字母后缀)
REAL_SKU = re.compile(r"\b[A-Z]{2,4}-\d{3,6}[A-Z0-9-]{0,4}\b")
# 真实商品品名需外部提供 (通用词无法可靠区分"真实商品"与"普通中文")
REAL_PRODUCT_WORDS = []

CRED_PATTERNS = [
    (re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*['\"][^'\"]{8,}"), "硬编码凭据"),
    (re.compile(r"(?i)\b(password|passwd|pass)\b\s*[:=]\s*['\"][^'\"]{4,}"), "硬编码口令"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥文件"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI-style key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"(?i)authorization\s*:\s*['\"]?(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"), "认证头"),
]
# 本机绝对路径 (含 Windows 用户名)
WIN_PATH = re.compile(r"[A-Za-z]:[\\/](?:Users|home)[\\/][^\\/\"'\s]{2,}")
POSIX_HOME = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]{2,}")

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}
BINARY_EXT = {".xlsx", ".xls", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".ico",
              ".pdf", ".pyc", ".woff", ".woff2", ".ttf", ".exe", ".dll", ".so"}


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def scan_text(path, root, findings, sku_black=None, use_exact_sku=False):
    rel = os.path.relpath(path, root)
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for lineno, line in enumerate(f, 1):
                for rx, kind in CRED_PATTERNS:
                    if rx.search(line):
                        findings.append((kind, rel, lineno, line.strip()[:110]))
                for m in REAL_SKU.finditer(line):
                    tok = m.group(0)
                    if use_exact_sku:
                        if tok.upper() in sku_black:
                            findings.append(("真实 SKU 编码", rel, lineno, tok))
                    else:
                        findings.append(("疑似商品编码(形态)", rel, lineno, tok))
                for w in REAL_PRODUCT_WORDS:
                    if w in line:
                        findings.append(("真实商品品名", rel, lineno, w))
                for rx in (WIN_PATH, POSIX_HOME):
                    m = rx.search(line)
                    if m:
                        findings.append(("本机绝对路径", rel, lineno, m.group(0)))
    except (OSError, UnicodeError):
        pass


def scan_zip(path, root, findings, sku_black=None, use_exact_sku=False):
    """在 zip/xlsx (本质是 zip) 的内部 XML 里找可疑字符串。"""
    rel = os.path.relpath(path, root)
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not (name.endswith(".xml") or name.endswith(".rels") or name.endswith(".txt")):
                    continue
                try:
                    data = z.read(name).decode("utf-8", errors="ignore")
                except (KeyError, OSError, RuntimeError):
                    continue
                for m in REAL_SKU.finditer(data):
                    tok = m.group(0)
                    if use_exact_sku:
                        if tok.upper() in sku_black:
                            findings.append(("真实 SKU 编码(内嵌)", f"{rel}!{name}", 0, tok))
                    else:
                        findings.append(("疑似商品编码(内嵌)", f"{rel}!{name}", 0, tok))
                for w in REAL_PRODUCT_WORDS:
                    if w in data:
                        findings.append(("真实商品品名(内嵌)", f"{rel}!{name}", 0, w))
                for rx, kind in CRED_PATTERNS:
                    if rx.search(data):
                        findings.append((f"{kind}(内嵌)", f"{rel}!{name}", 0, ""))
    except (zipfile.BadZipFile, OSError):
        pass


def load_blacklist(path):
    """读取外部黑名单 (每行一条, # 开头为注释)。"""
    out = set()
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.add(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="待发布目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--sku-blacklist", default=None,
                    help="外部 SKU 黑名单文件 (每行一条)")
    ap.add_argument("--words-blacklist", default=None,
                    help="外部商品名黑名单文件 (每行一条)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    global REAL_PRODUCT_WORDS
    REAL_PRODUCT_WORDS = sorted(load_blacklist(args.words_blacklist))
    sku_black = {s.upper() for s in load_blacklist(args.sku_blacklist)}
    # 外部 SKU 黑名单为空时, 不做「精确匹配」, 只报通用形态的疑似编码
    use_exact_sku = bool(sku_black)

    findings = []
    checked = 0

    # 1) 运行期产物目录
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, root)
        parts = rel.replace("\\", "/").split("/")
        for p in parts:
            if p in ("workspace", "out", "input", "in") or p.endswith("_output"):
                findings.append(("运行期产物目录(可能含订单数据)", rel, 0, p))
                break

    # 2) 文件内容
    for path in iter_files(root):
        checked += 1
        ext = os.path.splitext(path)[1].lower()
        if ext in BINARY_EXT:
            scan_zip(path, root, findings, sku_black, use_exact_sku)
        else:
            scan_text(path, root, findings, sku_black, use_exact_sku)

    # 去重
    uniq = []
    seen = set()
    for f in findings:
        k = (f[0], f[1], f[3][:60])
        if k not in seen:
            seen.add(k)
            uniq.append(f)

    # 分级: 高危 = 精确命中黑名单 / 凭据 / 绝对路径 / 产物目录
    HIGH = ("真实 SKU", "真实商品品名", "凭据", "私钥", "口令", "token", "key",
            "本机绝对路径", "运行期产物目录")
    high = [f for f in uniq if any(h in f[0] for h in HIGH)]
    low = [f for f in uniq if f not in high]

    if args.json:
        print(json.dumps({
            "root": root, "files_checked": checked,
            "clean": not high,
            "high_risk": [dict(kind=k, file=f, line=l, detail=d) for k, f, l, d in high],
            "low_risk": [dict(kind=k, file=f, line=l, detail=d) for k, f, l, d in low],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"扫描目录: {root}")
        print(f"检查文件: {checked}")
        if not use_exact_sku:
            print("提示: 未提供 SKU/品名黑名单, 仅按通用形态扫描 (精确命中需 --sku-blacklist)")
        if high:
            print(f"\n✗ 高危 {len(high)} 项 (必须处理):\n")
            for kind, f, l, d in high:
                loc = f"{f}:{l}" if l else f
                print(f"  [{kind}] {loc}  {d}")
        if low:
            print(f"\n! 提示级 {len(low)} 项 (形态疑似, 人工确认):\n")
            for kind, f, l, d in low:
                loc = f"{f}:{l}" if l else f
                print(f"  [{kind}] {loc}  {d}")
        if not high:
            print("\n✓ 无高危项 —— 可以发布")
    return 0 if not high else 1


if __name__ == "__main__":
    sys.exit(main())
