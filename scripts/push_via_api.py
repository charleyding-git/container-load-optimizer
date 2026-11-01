# -*- coding: utf-8 -*-
"""通过 GitHub REST API (Git Data API) 一次性推送整个目录到仓库。

比 `git push` 更稳: 只走 HTTPS + PAT, 不依赖 git 协议栈与代理的 CONNECT 支持。

用法:
    python scripts/push_via_api.py <本地目录> <owner/repo> [branch] [--message MSG]

代理:
    默认直连。若需要走代理, 按以下优先级设置环境变量:
      GH_PROXY        (专用, 优先级最高)
      HTTPS_PROXY / https_proxy
      HTTP_PROXY  / http_proxy
    例: export GH_PROXY=http://127.0.0.1:7890
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _resolve_proxy():
    """解析代理设置: GH_PROXY > 标准代理环境变量 > 直连 (空字符串)。

    默认不设代理 —— 通用环境下的合理默认; 需要代理时用环境变量指定。
    """
    for key in ("GH_PROXY", "HTTPS_PROXY", "https_proxy",
                "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return ""


PROXY = _resolve_proxy()
API = "https://api.github.com"


def get_token():
    """读取 GitHub PAT。

    优先级:
      1. 环境变量 GITHUB_TOKEN / GH_TOKEN
      2. ~/.git-credentials (git credential store 写入的格式)
    返回值不会打印到 stdout。
    """
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            return val

    path = os.path.expanduser("~/.git-credentials")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m = re.match(r"https://([^:]+):([^@]+)@(.+)", line)
                if m and m.group(3).startswith("github.com"):
                    return m.group(2)
    raise SystemExit(
        "找不到 GitHub PAT。请设置环境变量 GITHUB_TOKEN, "
        "或用 `git credential-store` 写入 ~/.git-credentials。")


TOKEN = get_token()
# 空 ProxyHandler 表示显式直连, 不让 urllib 再自行探测环境变量
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY} if PROXY else {}))


def api(method, path, body=None, raw=False):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "workbuddy-push",
        "Content-Type": "application/json",
    })
    try:
        with OPENER.open(req, timeout=60) as r:
            txt = r.read().decode("utf-8")
            return txt if raw else (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise SystemExit(f"{method} {path} → HTTP {e.code}\n{detail}")
    except urllib.error.URLError as e:
        last = e
        for attempt in range(4):
            time.sleep(1.5 * (attempt + 1))
            try:
                with OPENER.open(req, timeout=60) as r:
                    txt = r.read().decode("utf-8")
                    return txt if raw else (json.loads(txt) if txt else None)
            except (urllib.error.URLError, OSError):
                continue
        raise SystemExit(f"{method} {path} → 网络错误(重试4次仍失败): {last}")


def collect_files(root):
    """收集目录下所有文件 (跳过 .git 与忽略项)。"""
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv",
                 ".idea", ".vscode", "workspace"}
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append((rel, full))
    return sorted(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    msg = "Add container-load-optimizer"
    if "--message" in sys.argv:
        i = sys.argv.index("--message")
        msg = sys.argv[i + 1]
        args = [a for a in args if a != msg]
    root = os.path.abspath(args[0])
    repo = args[1]
    branch = args[2] if len(args) > 2 else "main"
    owner, name = repo.split("/")

    files = collect_files(root)
    print(f"待推送 {len(files)} 个文件 → {repo}@{branch}")

    # 1) 找 base commit (空仓库 → 无 ref, 稍后建 orphan commit)
    base_sha = None
    base_tree = None
    try:
        ref = api("GET", f"/repos/{repo}/git/ref/heads/{branch}")
        base_sha = ref["object"]["sha"]
        base_commit = api("GET", f"/repos/{repo}/git/commits/{base_sha}")
        base_tree = base_commit["tree"]["sha"]
        print(f"  base commit: {base_sha[:10]}  tree: {base_tree[:10]}")
    except SystemExit:
        print("  空仓库 (无 ref) → 将创建初始 commit")

    # 空仓库: GitHub 不允许直接建 blob, 改用 Contents API 逐文件写入
    if not base_sha:
        return push_via_contents(root, repo, branch, msg, files)

    # 2) 逐个建 blob
    entries = []
    for rel, full in files:
        with open(full, "rb") as f:
            blob = api("POST", f"/repos/{repo}/git/blobs",
                       {"content": base64.b64encode(f.read()).decode("ascii"),
                        "encoding": "base64"}, raw=False)
        entries.append({"path": rel, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
    print(f"  已创建 {len(entries)} 个 blob")

    # 3) 建 tree
    tree_body = {"tree": entries}
    if base_tree:
        tree_body["base_tree"] = base_tree
    tree = api("POST", f"/repos/{repo}/git/trees", tree_body)
    print(f"  tree: {tree['sha'][:10]}")

    # 4) 建 commit (空仓库 → 无 parents = 初始 commit)
    commit_body = {"message": msg, "tree": tree["sha"]}
    if base_sha:
        commit_body["parents"] = [base_sha]
    commit = api("POST", f"/repos/{repo}/git/commits", commit_body)
    print(f"  commit: {commit['sha'][:10]}")

    # 5) 更新 ref (空仓库 → 需要先创建 ref)
    try:
        api("PATCH", f"/repos/{repo}/git/refs/heads/{branch}",
            {"sha": commit["sha"], "force": False})
    except SystemExit:
        api("POST", f"/repos/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    print(f"  \u2713 \u5df2\u63a8\u9001 \u2192 https://github.com/{repo}/tree/{branch}")

    # 6) 验证
    time.sleep(1)
    tree2 = api("GET", f"/repos/{repo}/git/trees/{commit['sha']}?recursive=1")
    print(f"  验证: 远程树含 {len(tree2.get('tree', []))} 项")


def push_via_contents(root, repo, branch, msg, files):
    """空仓库首推: 用 Contents API 逐文件创建 (第一个文件建立分支, 其余追加)。"""
    print("  [Contents API 模式] 逐文件创建 ...")
    created = 0
    for i, (rel, full) in enumerate(files, 1):
        with open(full, "rb") as f:
            raw = f.read()
        # Contents API 一律要求 base64 编码
        body = {"message": f"{msg}\n\n[{i}/{len(files)}] {rel}",
                "content": base64.b64encode(raw).decode("ascii"),
                "branch": branch}
        # 幂等: 已存在且内容一致 → 跳过
        try:
            exist = api("GET", f"/repos/{repo}/contents/{urllib.parse.quote(rel)}")
            remote_sha = exist.get("sha")
            same = False
            if exist.get("content") is not None and exist.get("encoding") == "base64":
                same = exist["content"].strip() == body["content"].strip()
            if same:
                print(f"    跳过 (已一致): {rel}")
                continue
            body["sha"] = remote_sha
        except SystemExit:
            pass  # 不存在 → 新建
        api("PUT", f"/repos/{repo}/contents/{urllib.parse.quote(rel)}", body)
        created += 1
        if i % 5 == 0 or i == len(files):
            print(f"    {i}/{len(files)} 已创建")
    print(f"  \u2713 \u5df2\u63a8\u9001 {created} \u4e2a\u6587\u4ef6 \u2192 https://github.com/{repo}/tree/{branch}")


if __name__ == "__main__":
    main()
