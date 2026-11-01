# -*- coding: utf-8 -*-
"""本地网页应用 —— 后端 (纯 Python 标准库, 无需 pip 安装)

启动:
  python server.py [--port 8770] [--workdir <输出目录>]

API:
  GET  /                    → UI 页面
  GET  /api/catalog         → 柜型/托盘/约束选项
  POST /api/solve           → 上传 xlsx + config, 求解并返回结果摘要
  GET  /api/scenes          → 3D 场景数据 (供页面预览与 L3 校验)
  GET  /api/download/<name>  → 下载产物 (html / xlsx / json)
  POST /api/export          → 一键导出三件套
"""
import argparse
import json
import os
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, SCRIPTS)

from catalog import CONTAINERS, PALLETS, CONST, STACK_PATTERNS, LOAD_ORDER  # noqa: E402
import catalog as CAT  # noqa: E402
from engine import Constraints, SKU  # noqa: E402
import pipeline as PL  # noqa: E402
import export_xlsx as EX  # noqa: E402
import export_viz as EV  # noqa: E402
import i18n  # noqa: E402

WORKDIR = os.path.join(HERE, "workspace")
LOCK = threading.Lock()
STATE = {"plan": None, "meta": {}, "last_error": None}


def _json_default(o):
    if isinstance(o, set):
        return list(o)
    return str(o)


def solve(input_xlsx, config):
    """执行求解 + 校验, 返回 (plan, gate)。"""
    gate = PL.Gate()
    batches, meta = PL.parse_excel(input_xlsx)
    if not batches:
        raise ValueError("未从输入解析出任何批次, 请检查表头 (需含 SKU编码/长/宽/高/箱数)")
    all_skus = []
    for b in batches.values():
        all_skus.extend(b)
    batches["_all_skus"] = all_skus
    containers, batch_meta = PL.build_plan(batches, config, gate)
    color_map = PL.make_color_map(all_skus)
    plan = dict(
        meta=dict(source=meta["source"], generated=time.strftime("%Y-%m-%d %H:%M:%S"),
                  config=config, parse=meta,
                  total_cartons=sum(c["cartons"] for c in containers),
                  total_cbm=round(sum(c["cbm"] for c in containers), 3),
                  total_containers=len(containers),
                  color_map=color_map,
                  sku_list=[s.to_dict() for s in all_skus]),
        box=CONTAINERS[config["container"]],
        pal=dict(PALLETS[config.get("pallet", "US48")]),
        cons=PL.make_constraints(config["constraints"]).to_dict(),
        containers=containers,
        batches=batch_meta,
        validation=gate.summary(),
    )
    if config.get("custom_pallet"):
        plan["pal"].update(config["custom_pallet"])
    return plan, gate


def build_products(plan, outdir, alt_plan=None):
    """生成三件套 + 场景数据。返回产物路径 dict。

    alt_plan: 第二方案 (另一装载模式) 的 plan —— 3D 可视化会把两方案合并进
    同一 HTML, 使「模式」下拉可切换对比。
    """
    os.makedirs(outdir, exist_ok=True)
    pj = os.path.join(outdir, "plan.json")
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    viz = os.path.join(outdir, "装载方案_3D可视化.html")
    scenes = os.path.join(outdir, "scene_data.json")
    EV.export(plan, viz, scenes, alt_plan=alt_plan)
    xlsx = os.path.join(outdir, "装载方案_作业方案.xlsx")
    EX.export(plan, xlsx)
    products = dict(plan=pj, viz=viz, xlsx=xlsx, scenes=scenes)
    # 副方案 (另一模式) 的作业方案 —— 两模式各有独立的作业卡
    if alt_plan:
        alt_xlsx = os.path.join(outdir, "装载方案_作业方案_另一模式.xlsx")
        try:
            EX.export(alt_plan, alt_xlsx)
            products["alt_xlsx"] = alt_xlsx
        except Exception as e:
            sys.stderr.write(f"[导出] 副方案作业方案生成失败: {e}\n")
    return products


class Handler(BaseHTTPRequestHandler):
    server_version = "LoadOpt/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---------- 工具 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=_json_default))

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    # ---------- 路由 ----------
    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path in ("/", "/index.html"):
            fp = os.path.join(HERE, "index.html")
            if not os.path.exists(fp):
                return self._json({"error": "index.html 缺失"}, 404)
            with open(fp, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/catalog":
            lang = i18n.get_lang()
            return self._json(dict(
                containers=[dict(v, code=k, label=CAT.label_of(v, lang))
                            for k, v in CONTAINERS.items()],
                pallets=[dict(v, code=k, label=CAT.label_of(v, lang))
                         for k, v in PALLETS.items()],
                stack_patterns=STACK_PATTERNS,
                load_orders=LOAD_ORDER,
                support_policies=Constraints.SUPPORT_POLICY,
                constants=CONST,
                defaults=dict(container="40HQ", pallet="US48",
                              support_policy="balanced")))
        if path == "/api/scenes":
            plan = STATE.get("plan")
            if not plan:
                return self._json({"error": "尚无求解结果"}, 404)
            scenes = EV.build_scenes(plan, alt_plan=STATE.get("alt_plan"))
            return self._json(scenes)
        if path.startswith("/api/download/"):
            name = urllib.parse.unquote(path[len("/api/download/"):])
            fp = os.path.join(WORKDIR, "out", name)
            if not os.path.exists(fp):
                return self._json({"error": f"文件不存在: {name}"}, 404)
            ext = os.path.splitext(name)[1].lower()
            ctype = {".html": "text/html; charset=utf-8",
                     ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     ".json": "application/json; charset=utf-8"}.get(ext, "application/octet-stream")
            with open(fp, "rb") as f:
                return self._send(200, f.read(), ctype,
                                  {"Content-Disposition": f'attachment; filename*=UTF-8\'\'{name}'})
        if path == "/api/status":
            return self._json(dict(ready=STATE.get("plan") is not None,
                                   last_error=STATE.get("last_error"),
                                   meta=STATE.get("meta", {})))
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path == "/api/solve":
            return self._solve()
        if path == "/api/export":
            return self._export()
        return self._json({"error": "not found"}, 404)

    # ---------- 求解 ----------
    def _solve(self):
        try:
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                return self._json({"error": "需 multipart/form-data"}, 400)
            raw = self._body()
            boundary = ctype.split("boundary=")[-1].strip().strip('"')
            parts = _parse_multipart(raw, boundary)
            if "file" not in parts:
                if i18n.get_lang() == "en":
                    return self._json({"error": "Missing uploaded file"}, 400)
                return self._json({"error": "缺少上传文件"}, 400)
            fname, fdata = parts["file"]
            cfg_raw = parts.get("config", ("", b"{}"))[1].decode("utf-8")
            config = json.loads(cfg_raw)
            # 界面语言随请求下发 —— 校验报告与模式名都按此语言生成
            if config.get("lang"):
                i18n.set_lang(config["lang"])
            os.makedirs(os.path.join(WORKDIR, "in"), exist_ok=True)
            in_path = os.path.join(WORKDIR, "in", os.path.basename(fname) or "input.xlsx")
            with open(in_path, "wb") as f:
                f.write(fdata)
            t0 = time.time()
            with LOCK:
                plan, gate = solve(in_path, config)
                # 双模式: 同时算另一装载方式, 合并进 3D 可视化供「模式」下拉切换
                alt_plan = None
                alt_gate = None
                if config.get("solve_both"):
                    alt_cfg = json.loads(json.dumps(config))
                    cur_mode = config.get("constraints", {}).get("mode", "pallet")
                    alt_cfg["constraints"]["mode"] = "nopallet" if cur_mode == "pallet" else "pallet"
                    try:
                        alt_plan, alt_gate = solve(in_path, alt_cfg)
                    except Exception as e2:
                        sys.stderr.write(f"[双模式] 副方案求解失败: {e2}\n")
                        alt_plan = None
                products = build_products(plan, os.path.join(WORKDIR, "out"),
                                          alt_plan=alt_plan)
                STATE["plan"] = plan
                STATE["alt_plan"] = alt_plan
                STATE["last_error"] = None
                STATE["meta"] = dict(elapsed=round(time.time() - t0, 1),
                                     products={k: os.path.basename(v) for k, v in products.items()})
            return self._json(dict(
                ok=True,
                elapsed=round(time.time() - t0, 1),
                containers=plan["meta"]["total_containers"],
                cartons=plan["meta"]["total_cartons"],
                cbm=plan["meta"]["total_cbm"],
                batches=plan["batches"],
                validation=plan["validation"],
                alt_validation=(alt_gate.summary() if alt_gate else None),
                products={k: os.path.basename(v) for k, v in products.items()},
                scenes=EV.build_scenes(plan, alt_plan=alt_plan),
                constraints=PL.make_constraints(config["constraints"]).describe(),
                gate_report=gate.report("校验报告")))
        except Exception as e:
            tb = traceback.format_exc()
            STATE["last_error"] = f"{type(e).__name__}: {e}"
            sys.stderr.write(tb + "\n")
            return self._json(dict(ok=False, error=f"{type(e).__name__}: {e}",
                                   traceback=tb.splitlines()[-6:]), 500)

    # ---------- 导出 ----------
    def _export(self):
        plan = STATE.get("plan")
        if not plan:
            return self._json({"error": "尚无求解结果"}, 404)
        raw = self._body()
        try:
            opts = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            opts = {}
        outdir = os.path.join(WORKDIR, "out")
        os.makedirs(outdir, exist_ok=True)
        products = build_products(plan, outdir, alt_plan=STATE.get("alt_plan"))
        # 打包 zip
        import zipfile
        zp = os.path.join(outdir, "装载方案_全部产物.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for k in ("viz", "xlsx", "plan", "scenes", "alt_xlsx"):
                if k in products:
                    z.write(products[k], arcname=os.path.basename(products[k]))
        return self._json(dict(ok=True,
                               files={k: os.path.basename(v) for k, v in products.items()},
                               zip=os.path.basename(zp),
                               outdir=outdir))


def _parse_multipart(raw, boundary):
    """极简 multipart 解析 (只处理本工具需要的字段)。"""
    out = {}
    sep = ("--" + boundary).encode()
    parts = raw.split(sep)
    for part in parts:
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        part = part.lstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body.rstrip(b"\r\n")
        headers = head.decode("utf-8", "ignore")
        name = ""
        filename = ""
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-disposition"):
                for part2 in line.split(";"):
                    part2 = part2.strip()
                    if part2.startswith("name="):
                        name = part2[5:].strip('"')
                    elif part2.startswith("filename="):
                        filename = part2[9:].strip('"')
        if name:
            out[name] = (filename, body)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    os.makedirs(WORKDIR, exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"装载优化 UI 已启动:  http://{a.host}:{a.port}")
    print(f"工作目录: {WORKDIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
