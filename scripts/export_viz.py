# -*- coding: utf-8 -*-
"""从 plan.json 生成 3D 交互式装载可视化 (单文件 HTML)

关键设计: 渲染器 **零布局逻辑**, 直接消费 plan.json 里求解器算好的精确 3D 箱位。
这样从结构上杜绝「渲染与方案不一致」。

同时输出 scene_data.json —— 供 L3 一致性校验读取 (与 HTML 内嵌数据同源)。
"""
import argparse
import json
import os

import i18n
import catalog as CAT

# ============================================================
# 3D 可视化页面的静态文案 (中英)
# ============================================================
VIZ_TEXT = {
    "zh": {
        "SUB": "鼠标拖拽旋转 · 滚轮缩放 · Shift+拖拽平移 · 「播放装载顺序」逐段查看装柜过程",
        "ALLB": "全部批次",
        "ALLM": "全部模式",
        "PREV": "◀ 上一柜",
        "NEXT": "下一柜 ▶",
        "PLAY": "▶ 播放装载顺序",
        "STOP": "■ 停止播放",
        "RESET": "重置视角",
        "WIRE": "线框模式",
        "SAVE": "⬇ 导出本页 HTML",
        "TIP": (
            "<b>读图说明</b>：柜体线框为集装箱内壁；<span style=\"color:#dc2626\">红色虚线</span>标注柜门开口高度上限"
            "（__DOOR__mm），超出该高度的货垛在实际作业中无法拉入柜门。<br>"
            "<b>棕色薄板</b>＝木托盘；彩色方块＝纸箱，每个箱子的位置都由求解器算出的精确底脚印决定，"
            "与 Excel 作业方案、plan.json 三方同源。<br>"
            "<b>关于「不同颜色夹在一起」</b>：若方案允许混装，这是「层间混装」设计本身 —— "
            "托盘以主SKU打底，层高不整除时插入高度互补的另一SKU填隙，顶部再做平躺散放。"
            "若方案禁止混装，则每个托盘只会出现一种颜色。<br>"
            "<b>支撑率与外部加固</b>：纸箱短时悬空不会失效（抗弯余量实测 100~50000 倍），"
            "是否压溃取决于「上方累积载荷 vs 承压能力」，轻抛货影响很小。"
            "因此本方案支撑率不达标处由外部手段兜底：<b>整体缠绕膜 + 十字捆扎 + 层间垫板 + 重货置底</b>，"
            "详见 Excel 作业方案「外部加固与作业要求」章节。"
        ),
        "DOOR": "门开口高 ",
        "DOOREND": "◀ 柜门端",
        "CTR": "柜",
        "CARTONS": "纸箱",
        "VOLUME": "货量",
        "UTIL": "装载率",
        "TOPH": "顶高",
        "PALLETS": "托盘",
        "LEGEND_PALLET": "木托盘",
        "LEGEND_BARE": "柜底裸地散箱",
        "STRUCTURE": "结构",
    },
    "en": {
        "SUB": "Drag to rotate · scroll to zoom · Shift+drag to pan · use “Play loading sequence” to step through the load",
        "ALLB": "All batches",
        "ALLM": "All modes",
        "PREV": "◀ Previous",
        "NEXT": "Next ▶",
        "PLAY": "▶ Play loading sequence",
        "STOP": "■ Stop",
        "RESET": "Reset view",
        "WIRE": "Wireframe",
        "SAVE": "⬇ Export this page as HTML",
        "TIP": (
            "<b>How to read this</b>: the wireframe is the container's inner wall; the "
            "<span style=\"color:#dc2626\">red dashed line</span> marks the door opening height limit"
            " (__DOOR__mm) — anything higher cannot physically be pushed through the door.<br>"
            "<b>Brown slabs</b> = wooden pallets; coloured blocks = cartons, each positioned from the exact "
            "footprint computed by the solver and shared with the Excel job card and plan.json.<br>"
            "<b>Why colours are mixed</b>: when the plan allows mixing, this is the mixed-layer design itself — "
            "a pallet is based on its primary SKU, and when the layer height does not divide evenly a "
            "height-complementary SKU is inserted to fill the gap; the top layer may carry flat-stacked boxes. "
            "If mixing is disabled, each pallet shows a single colour.<br>"
            "<b>Support ratio and external securing</b>: short-term unsupported cartons do not fail "
            "(measured bending margin is 100~50000×); what actually breaks is local crushing, i.e. cumulative "
            "load above vs bearing capacity — light goods are barely affected. Where the support ratio is below "
            "target, this plan relies on external securing: <b>full stretch wrap + cross strapping + "
            "interlayer boards + heavy goods at the bottom</b>; see the “External securing and handling "
            "requirements” section of the Excel job card."
        ),
        "DOOR": "Door opening height ",
        "DOOREND": "◀ Door end",
        "CTR": "Ctr ",
        "CARTONS": "Cartons",
        "VOLUME": "Volume",
        "UTIL": "Utilization",
        "TOPH": "Top height",
        "PALLETS": "Pallets",
        "LEGEND_PALLET": "Wooden pallet",
        "LEGEND_BARE": "Bare-floor cartons",
        "STRUCTURE": "Structure",
    },
}

TPL = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0f172a;color:#e2e8f0;
font-family:Inter,"Segoe UI",system-ui,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1320px;margin:0 auto;padding:20px}
h1{font-size:19px;margin:0 0 4px}
.sub{font-size:12.5px;color:#94a3b8;margin-bottom:14px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
button,select{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:7px;
padding:7px 13px;font-size:13px;cursor:pointer;font-family:inherit}
button:hover,select:hover{background:#334155}
button.on{background:#2563eb;border-color:#2563eb;color:#fff}
.spacer{flex:1}
.stage{background:#f8fafc;border-radius:12px;overflow:hidden;position:relative;
box-shadow:0 4px 24px rgba(0,0,0,.3)}
#cv{display:block;width:100%;height:740px;cursor:grab}
#cv:active{cursor:grabbing}
.hud{position:absolute;left:14px;top:14px;background:rgba(15,23,42,.9);padding:11px 15px;
border-radius:9px;font-size:12.5px;line-height:1.85;pointer-events:none}
.hud b{color:#fff}.hud .k{color:#94a3b8}
.legend{display:flex;flex-wrap:wrap;gap:5px 13px;margin-top:12px;font-size:12px;color:#cbd5e1}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;
vertical-align:-1px;border:1px solid rgba(255,255,255,.25)}
.legend em{color:#64748b;font-style:normal;font-size:10.5px}
.tip{margin-top:9px;font-size:12px;color:#94a3b8;line-height:1.85}
.tip b{color:#cbd5e1}
</style></head><body><div class="wrap">
<h1>__TITLE__</h1>
<div class="sub">__T_SUB__</div>
<div class="bar">
  <select id="batch"><option value="__">__T_ALLB__</option></select>
  <select id="mode"><option value="__">__T_ALLM__</option></select>
  <button id="prev">__T_PREV__</button><button id="next">__T_NEXT__</button>
  <button id="play">__T_PLAY__</button><button id="reset">__T_RESET__</button>
  <div class="spacer"></div><button id="wire">__T_WIRE__</button>
  <button id="save">__T_SAVE__</button>
</div>
<div class="stage"><canvas id="cv"></canvas><div class="hud" id="hud"></div></div>
<div class="legend" id="legend"></div>
<div class="tip">
 __T_TIP__
</div></div>
<script>
const SCENES=__SCENES_DATA__;
const LEGEND=__LEGEND__;
const PALCOL=__PALCOL__;
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let box=SCENES.ct, bi='__', mi='__', ci=0;
let yaw=1.05,pitch=0.42,zoom=1,panX=0,panY=0,camDist=1.25,focal=6400;
let drag=false,lx=0,ly=0,wire=false,playing=false,playT=0;

function list(){const a=SCENES.items.filter(s=>(mi==='__'||s.mode===mi)&&(bi==='__'||s.batch===bi));return a;}
function cur(){const l=list();return l[ci]||l[0];}
function resize(){const r=cv.getBoundingClientRect(),d=window.devicePixelRatio||1;
 cv.width=Math.round(r.width*d);cv.height=Math.round(r.height*d);ctx.setTransform(d,0,0,d,0,0);}
window.addEventListener('resize',resize);
cv.addEventListener('mousedown',e=>{drag=true;lx=e.clientX;ly=e.clientY;});
window.addEventListener('mouseup',()=>drag=false);
window.addEventListener('mousemove',e=>{if(!drag)return;
 const dx=e.clientX-lx,dy=e.clientY-ly;lx=e.clientX;ly=e.clientY;
 if(e.shiftKey){panX+=dx;panY+=dy;}
 else{yaw+=dx*0.006;pitch+=dy*0.005;pitch=Math.max(-0.3,Math.min(1.35,pitch));}
 draw();});
cv.addEventListener('wheel',e=>{e.preventDefault();
 camDist*=(e.deltaY>0?1.08:0.93);camDist=Math.max(0.9,Math.min(6,camDist));draw();},{passive:false});

function draw(){
 const C=box,c=cur(),W=cv.clientWidth,H=cv.clientHeight;
 if(!c){ctx.fillStyle='#f8fafc';ctx.fillRect(0,0,W,H);return;}
 ctx.fillStyle='#f8fafc';ctx.fillRect(0,0,W,H);
 const cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(pitch),sx=Math.sin(pitch);
 const ox=W/2+panX,oy=H/2+panY;
 const CAM_D=22000*camDist;
 function depthH(x,y){const a=x-C.L/2,b=y-C.W/2;return (a*sy+b*cy)*cx+CAM_D;}
 function cam3(x,y,z){const a=x-C.L/2,b=y-C.W/2,cc=z-C.H/2;
  const rx=a*cy-b*sy,ry=a*sy+b*cy,rz=cc;
  const ey=ry*cx-rz*sx,ez=ry*sx+rz*cx;
  const depth=ey+CAM_D,p=focal/Math.max(depth,1500);
  return [rx*p,-ez*p,depth];}
 const probe=[];
 for(const cx0 of [0,C.L])for(const cy0 of [0,C.W])probe.push(cam3(cx0,cy0,0));
 for(const b of (c.boxes||[]))for(const d of [[0,0,0],[b.w,0,0],[b.w,b.d,0],[0,b.d,0],
   [0,0,b.h],[b.w,0,b.h],[b.w,b.d,b.h],[0,b.d,b.h]])probe.push(cam3(b.x+d[0],b.y+d[1],b.z+d[2]));
 let mnX=1e18,mxX=-1e18,mnY=1e18,mxY=-1e18;
 for(const p of probe){if(p[0]<mnX)mnX=p[0];if(p[0]>mxX)mxX=p[0];
  if(p[1]<mnY)mnY=p[1];if(p[1]>mxY)mxY=p[1];}
 const bw=Math.max(mxX-mnX,1),bh=Math.max(mxY-mnY,1);
 const S=Math.min((W-50)/bw,(H-50)/bh)*0.98*zoom;
 const ox2=(mnX+mxX)/2,oy2=(mnY+mxY)/2;
 function V(x,y,z){const p=cam3(x,y,z);return [ox+(p[0]-ox2)*S,oy+(p[1]-oy2)*S,p[2]];}
 ctx.beginPath();
 ctx.moveTo(...V(0,0,0));ctx.lineTo(...V(C.L,0,0));ctx.lineTo(...V(C.L,C.W,0));
 ctx.lineTo(...V(0,C.W,0));ctx.closePath();
 ctx.fillStyle='#e2e8f0';ctx.fill();ctx.strokeStyle='#0f172a';ctx.lineWidth=1.6;ctx.stroke();
 let boxes=(c.boxes||[]).slice();
 if(playing)boxes=boxes.slice(0,Math.max(1,Math.floor(boxes.length*playT)));
 const B=boxes.map(b=>({...b,depth:depthH(b.x+b.w/2,b.y+b.d/2)}));
 B.sort((p,q)=>q.depth-p.depth);
 for(const b of B){
  const x0=b.x,x1=b.x+b.w,y0=b.y,y1=b.y+b.d,z0=b.z,z1=b.z+b.h;
  const pal=b.kind==='pallet';const bare=b.kind==='bare';
  ctx.beginPath();ctx.moveTo(...V(x1,y0,z0));ctx.lineTo(...V(x1,y1,z0));
  ctx.lineTo(...V(x1,y1,z1));ctx.lineTo(...V(x1,y0,z1));ctx.closePath();
  ctx.fillStyle=b.c;ctx.globalAlpha=wire?0.05:(bare?0.55:(pal?0.30:0.32));ctx.fill();ctx.globalAlpha=1;
  if(bare){ctx.strokeStyle='#ea580c';ctx.lineWidth=1.3;ctx.setLineDash([5,3]);}else{ctx.strokeStyle='rgba(15,23,42,.28)';ctx.lineWidth=0.3;}ctx.stroke();ctx.setLineDash([]);
  ctx.beginPath();ctx.moveTo(...V(x0,y0,z0));ctx.lineTo(...V(x1,y0,z0));
  ctx.lineTo(...V(x1,y0,z1));ctx.lineTo(...V(x0,y0,z1));ctx.closePath();
  ctx.fillStyle=b.c;ctx.globalAlpha=wire?0.06:(pal?0.42:0.45);ctx.fill();ctx.globalAlpha=1;
  ctx.strokeStyle='rgba(15,23,42,.35)';ctx.lineWidth=0.3;ctx.stroke();
  ctx.beginPath();ctx.moveTo(...V(x0,y0,z1));ctx.lineTo(...V(x1,y0,z1));
  ctx.lineTo(...V(x1,y1,z1));ctx.lineTo(...V(x0,y1,z1));ctx.closePath();
  ctx.fillStyle=b.c;ctx.globalAlpha=wire?0.12:(pal?0.62:0.68);ctx.fill();ctx.globalAlpha=1;
  ctx.strokeStyle='rgba(15,23,42,.5)';ctx.lineWidth=0.4;ctx.stroke();
 }
 ctx.strokeStyle='#0f172a';ctx.lineWidth=1.6;
 for(const e of [[0,0,C.H],[C.L,0,C.H],[C.L,C.W,C.H],[0,C.W,C.H],[0,0,0],[0,0,C.H],
   [C.L,0,0],[C.L,0,C.H],[C.L,C.W,0],[C.L,C.W,C.H],[0,C.W,0],[0,C.W,C.H]]){
  ctx.beginPath();ctx.moveTo(...V(e[0],e[1],e[2]));ctx.lineTo(...V(e[3],e[4],e[5]));ctx.stroke();}
 const dl=V(0,0,C.doorH),dr=V(0,C.W,C.doorH);
 ctx.save();ctx.strokeStyle='#dc2626';ctx.lineWidth=2.8;ctx.setLineDash([8,4]);
 ctx.beginPath();ctx.moveTo(dl[0],dl[1]);ctx.lineTo(dr[0],dr[1]);ctx.stroke();ctx.restore();
 ctx.fillStyle='#dc2626';ctx.font='600 12.5px sans-serif';
 ctx.fillText('__T_DOOR__'+C.doorH+'mm',dl[0]-6,dl[1]-9);
 const dmid=V(0,C.W/2,0);ctx.fillStyle='#334155';ctx.font='600 11.5px sans-serif';
 ctx.fillText('__T_DOOREND__',dmid[0]-46,dmid[1]+16);
 document.getElementById('hud').innerHTML=
  `<b>${c.mode_label||''} · ${c.batch||''} · ${box.label} · __T_CTR__${c.local_idx||c.idx} / ${list().length}</b><br>`+
  `<span class="k">__T_CARTONS__</span> <b>${c.cartons}</b> &nbsp;<span class="k">__T_VOLUME__</span> <b>${c.cbm}</b> m³<br>`+
  `<span class="k">__T_UTIL__</span> <b>${c.util}%</b> &nbsp;<span class="k">__T_TOPH__</span> <b>${c.hmax}</b> mm<br>`+
  `<span class="k">__T_PALLETS__</span> <b>${c.npallets||0}</b> &nbsp;<span class="k">__T_STRUCTURE__</span> <b>${c.note||''}</b>`;
}
document.getElementById('mode').onchange=e=>{mi=e.target.value;ci=0;syncBatchOptions();draw();};
document.getElementById('batch').onchange=e=>{bi=e.target.value;ci=0;draw();};
// 按当前 mode 过滤后重建批次下拉选项 (合并两方案后批次集合可能不同)
function syncBatchOptions(){
 const sel=document.getElementById('batch');
 const opts=['__'].concat([...new Set(SCENES.items.filter(s=>mi==='__'||s.mode===mi).map(s=>s.batch))]);
 const cur2=sel.value;
 sel.innerHTML=opts.map(b=>`<option value="${b}">${b==='__'?'全部批次':b}</option>`).join('');
 bi=opts.includes(cur2)?cur2:'__';
 sel.value=bi;
}
document.getElementById('prev').onclick=()=>{const n=list().length;if(!n)return;ci=(ci-1+n)%n;draw();};
document.getElementById('next').onclick=()=>{const n=list().length;if(!n)return;ci=(ci+1)%n;draw();};
document.getElementById('reset').onclick=()=>{yaw=1.05;pitch=0.42;zoom=1;panX=0;panY=0;camDist=1.25;focal=6400;draw();};
document.getElementById('wire').onclick=e=>{wire=!wire;e.target.classList.toggle('on',wire);draw();};
document.getElementById('save').onclick=()=>{
 const blob=new Blob(['<!DOCTYPE html>\n'+document.documentElement.outerHTML],{type:'text/html'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);
 a.download='__FILENAME__';a.click();};
document.getElementById('play').onclick=e=>{
 if(playing){playing=false;e.target.textContent='__T_PLAY__';e.target.classList.remove('on');return;}
 playing=true;playT=0;e.target.textContent='__T_STOP__';e.target.classList.add('on');
 const step=()=>{if(!playing)return;playT=Math.min(1,playT+0.006);draw();
  if(playT>=1){playing=false;document.getElementById('play').textContent='__T_PLAY__';
   document.getElementById('play').classList.remove('on');return;}
  requestAnimationFrame(step);};
 requestAnimationFrame(step);};
window.__setView=(y,p,d)=>{yaw=y;pitch=p;if(d)camDist=d;draw();};
// (batch, idxInFilteredList, mode) —— 供 L4 视觉审查定位场景
window.__setScene=(b,i,m)=>{bi=b;if(m)mi=m;ci=i||0;syncBatchOptions();draw();};
// 按「全局序号」定位场景 (合并多方案后 idx 可能跨模式重复, 故用全局序号)
window.__setSceneByIndex=(k)=>{const it=SCENES.items[k];if(!it)return;mi=it.mode;bi=it.batch;
 const l=list();ci=Math.max(0,l.indexOf(it));syncBatchOptions();draw();};
window.__getScene=()=>cur();
window.SCENES=SCENES;   // 供 L3/L4 一致性校验读取
document.getElementById('legend').innerHTML=LEGEND.map(s=>
 `<span><i style="background:${s.c}"></i>${s.sku} ${s.name}</span>`).join('')
 +`<span><i style="background:#b45309"></i>__T_LEGEND_PALLET__</span>`
 +`<span><i style="background:#ea580c"></i>__T_LEGEND_BARE__</span>`;
resize();
// 模式下拉按数据生成 (合并多方案时自动出现对应选项)
(function(){
 const sel=document.getElementById('mode');
 const ms=SCENES.modes||[];
 sel.innerHTML='<option value="__">全部模式</option>'+ms.map(m=>
   `<option value="${m.code}">${m.label}</option>`).join('');
 mi='__';
 syncBatchOptions();
})();
resize();draw();
</script></body></html>"""


def build_scenes(plan, alt_plan=None, alt_name=None):
    """从 plan.json 构造 3D 场景数据 (与 xlsx 同源)。

    支持**多方案合并**: 传入 alt_plan (另一装载模式, 如无托盘) 时, 两个方案的柜
    合并进同一份 scenes, 每项带 "mode" 标签 ("A"=无托盘 / "B"=有托盘),
    前端「模式」下拉据此筛选 —— 这样一个 HTML 就能对比两种装载方式。

    alt_name: alt_plan 的显示名 (写入场景项的 mode_label)。
    """
    meta = plan["meta"]
    cmap = meta.get("color_map", {})
    items = []
    modes = []

    def _mode_of(p):
        return "B" if p.get("cons", {}).get("mode", "pallet") == "pallet" else "A"

    def _label_of(p, is_alt):
        """模式显示名 (跟随 i18n 当前语言)。"""
        en = (i18n.get_lang() == "en")
        if is_alt and alt_name:
            return alt_name
        is_pallet = p.get("cons", {}).get("mode") == "pallet"
        if en:
            return "Mode B — palletized" if is_pallet else "Mode A — no pallet"
        return "模式B 有托盘" if is_pallet else "模式A 无托盘"

    def _collect(p, is_alt, idx_offset):
        m = _mode_of(p)
        lbl = _label_of(p, is_alt)
        for c in p["containers"]:
            boxes = []
            is_pallet_mode = p.get("cons", {}).get("mode") == "pallet"
            for pl in c.get("pallets", []):
                if is_pallet_mode:
                    boxes.append(dict(x=pl["cx"] - pl["w"] / 2, y=pl["cy"] - pl["d"] / 2,
                                      z=0, w=pl["w"], d=pl["d"], h=p["pal"]["H"],
                                      c="#b45309", kind="pallet", sku=None))
                for b in pl.get("boxes", []):
                    boxes.append(dict(x=b["x"], y=b["y"], z=b["z"], w=b["w"], d=b["d"],
                                      h=b["h"], c=cmap.get(b["sku"], "#64748b"),
                                      kind=b.get("kind", "layer"), sku=b["sku"]))
            for st in c.get("stacks", []):
                if "boxes" in st:
                    for b in st["boxes"]:
                        boxes.append(dict(x=b["x"], y=b["y"], z=b["z"], w=b["w"], d=b["d"],
                                          h=b["h"], c=cmap.get(b["sku"], "#64748b"),
                                          kind="layer", sku=b["sku"]))
            for st in c.get("bare", []):
                boxes.append(dict(x=st["x"], y=st["y"], z=st["z"], w=st["fx"], d=st["fy"],
                                  h=st["h"], c=cmap.get(st["sku"], "#64748b"),
                                  kind="bare", sku=st["sku"]))
            items.append(dict(idx=c["idx"] + idx_offset, local_idx=c["idx"],
                               mode=m, mode_label=lbl,
                               batch=c.get("batch", ""),
                               cartons=c["cartons"], cbm=c["cbm"], util=c["util"],
                               hmax=c["hmax"], npallets=c.get("npallets", 0),
                               note=c.get("note", ""), boxes=boxes))
        return m, lbl

    m_main, lbl_main = _collect(plan, False, 0)
    modes.append(dict(code=m_main, label=lbl_main))
    if alt_plan:
        off = max([i["idx"] for i in items] or [0])
        m_alt, lbl_alt = _collect(alt_plan, True, off)
        modes.append(dict(code=m_alt, label=lbl_alt))

    box = dict(plan["box"])
    box["label"] = CAT.label_of(plan["box"], i18n.get_lang())
    legend = [dict(sku=s["code"], name=s["name"], c=cmap.get(s["code"], "#64748b"))
              for s in meta["sku_list"]]
    batches = sorted(set(i["batch"] for i in items))
    palcol = {p["pi"]: "#b45309" for p in []}
    return dict(ct=box, items=items, batches=batches, modes=modes)


def export(plan, out_path, scene_path=None, alt_plan=None, alt_name=None):
    scenes = build_scenes(plan, alt_plan=alt_plan, alt_name=alt_name)
    meta = plan["meta"]
    legend = [dict(sku=s["code"], name=s["name"], c=meta["color_map"].get(s["code"], "#64748b"))
              for s in meta["sku_list"]]
    # 页面静态文案按当前语言注入
    VT = VIZ_TEXT.get(i18n.get_lang(), VIZ_TEXT["zh"])
    html = (TPL
            .replace("__TITLE__", f"集装箱装载优化 · 3D 可视化 · {plan['box'].get('label','')}"
                     if i18n.get_lang() != "en" else
                     f"Container Load Optimizer · 3D visualization · {plan['box'].get('label','')}")
            .replace("__DOOR__", str(plan["box"]["doorH"]))
            .replace("__SCENES_DATA__", json.dumps(scenes, ensure_ascii=False, separators=(",", ":")))
            .replace("__LEGEND__", json.dumps(legend, ensure_ascii=False))
            .replace("__PALCOL__", json.dumps({}, ensure_ascii=False))
            .replace("__FILENAME__", os.path.basename(out_path))
            .replace("__T_SUB__", VT["SUB"])
            .replace("__T_ALLB__", VT["ALLB"])
            .replace("__T_ALLM__", VT["ALLM"])
            .replace("__T_PREV__", VT["PREV"])
            .replace("__T_NEXT__", VT["NEXT"])
            .replace("__T_PLAY__", VT["PLAY"])
            .replace("__T_RESET__", VT["RESET"])
            .replace("__T_WIRE__", VT["WIRE"])
            .replace("__T_SAVE__", VT["SAVE"])
            .replace("__T_TIP__", VT["TIP"]))
    # 动态文案 (JS 内) 也随语言
    html = html.replace("__T_STOP__", VT["STOP"])
    html = html.replace("__T_DOOR__", VT["DOOR"])
    html = html.replace("__T_DOOREND__", VT["DOOREND"])
    html = html.replace("__T_CTR__", VT["CTR"])
    html = html.replace("__T_CARTONS__", VT["CARTONS"])
    html = html.replace("__T_VOLUME__", VT["VOLUME"])
    html = html.replace("__T_UTIL__", VT["UTIL"])
    html = html.replace("__T_TOPH__", VT["TOPH"])
    html = html.replace("__T_PALLETS__", VT["PALLETS"])
    html = html.replace("__T_LEGEND_PALLET__", VT["LEGEND_PALLET"])
    html = html.replace("__T_LEGEND_BARE__", VT["LEGEND_BARE"])
    html = html.replace("__T_STRUCTURE__", VT["STRUCTURE"])
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    if scene_path:
        with open(scene_path, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, separators=(",", ":"))
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scenes", default=None)
    ap.add_argument("--alt-plan", default=None,
                    help="合并第二方案 (如无托盘) 的 plan.json, 使模式下拉可切换")
    ap.add_argument("--alt-name", default=None,
                    help="第二方案的显示名 (默认按 mode 自动命名)")
    ap.add_argument("--lang", default=None, choices=["zh", "en"],
                    help="页面语言 (默认: zh; env WB_LANG=en 也可)")
    a = ap.parse_args()
    if a.lang:
        i18n.set_lang(a.lang)
    else:
        i18n.resolve_cli_lang()
    with open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    alt = None
    if a.alt_plan:
        with open(a.alt_plan, encoding="utf-8") as f:
            alt = json.load(f)
    p = export(plan, a.out, a.scenes, alt_plan=alt, alt_name=a.alt_name)
    print(f"→ {p}")
