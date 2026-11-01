// L4 视觉审查 —— 渲染截图 + 页面内几何自校验
// 用法: node visual_check.mjs <html路径> <输出目录> [柜号列表]
//
// Playwright 路径解析顺序 (不写死任何机器的绝对路径):
//   1. 环境变量 PLAYWRIGHT_MODULE / CHROMIUM_PATH
//   2. 本地 node_modules 里的 @playwright/cli
//   3. 由 playwright 自带的 chromium (chromium.launch() 不传 executablePath)
import path from 'path';
import fs from 'fs';
import { fileURLToPath, pathToFileURL } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const HTML = process.argv[2] || 'http://127.0.0.1:8899/装载方案_3D可视化.html';
const OUT = process.argv[3] || path.resolve('shots_l4');
const CH = process.argv[4] ? process.argv[4].split(',').map(Number) : null;

fs.mkdirSync(OUT, { recursive: true });

// ---- 定位 playwright 模块 ----
async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_MODULE,
    path.join(HERE, 'node_modules', '@playwright', 'cli', 'node_modules', 'playwright', 'index.mjs'),
    path.join(HERE, '..', 'node_modules', '@playwright', 'cli', 'node_modules', 'playwright', 'index.mjs'),
  ].filter(Boolean);

  // WorkBuddy 运行时目录: ~/.workbuddy/binaries/node/versions/<ver>/node_modules/@playwright/cli/...
  // 用 glob 探测而非写死版本号与用户名
  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (home) {
    const base = path.join(home, '.workbuddy', 'binaries', 'node', 'versions');
    try {
      for (const ver of fs.readdirSync(base)) {
        candidates.push(path.join(base, ver, 'node_modules', '@playwright', 'cli',
          'node_modules', 'playwright', 'index.mjs'));
      }
    } catch (e) { /* 目录不存在则跳过 */ }
  }

  candidates.push('playwright', 'playwright-core');

  for (const c of candidates) {
    try {
      const spec = c.startsWith('.') || path.isAbsolute(c) ? pathToFileURL(c).href : c;
      const mod = await import(spec);
      return mod.chromium;
    } catch (e) { /* try next */ }
  }
  throw new Error(
    '找不到 Playwright。请先安装:  npm i -D @playwright/cli\n' +
    '或设置环境变量 PLAYWRIGHT_MODULE 指向 playwright 的 index.mjs。'
  );
}

const chromium = await loadChromium();

// ---- 定位 chromium 可执行文件 (可选; 找不到时交给 playwright 自带) ----
// 候选按「版本号降序」排列: 较新版本的 chromium 通常修过旧版的 ICU/启动问题
function findChromiumCandidates() {
  if (process.env.CHROMIUM_PATH) return [process.env.CHROMIUM_PATH];
  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (!home) return [];
  const roots = [
    path.join(home, 'AppData', 'Local', 'ms-playwright'),
    path.join(home, '.cache', 'ms-playwright'),
    path.join(home, 'Library', 'Caches', 'ms-playwright'),
  ];
  const found = [];
  for (const root of roots) {
    let dirs = [];
    try { dirs = fs.readdirSync(root); } catch (e) { continue; }
    // chromium-1234 → 1234 (数字越大越新)
    dirs.sort((a, b) => {
      const na = parseInt((a.match(/(\d+)$/) || [0, 0])[1], 10);
      const nb = parseInt((b.match(/(\d+)$/) || [0, 0])[1], 10);
      return nb - na;
    });
    for (const d of dirs) {
      for (const rel of [
        ['chrome-win64', 'chrome.exe'],
        ['chrome-win', 'chrome.exe'],
        ['chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'],
        ['chrome-linux', 'chrome'],
      ]) {
        const p = path.join(root, d, ...rel);
        if (fs.existsSync(p)) found.push(p);
      }
    }
  }
  return found;
}

// 依次尝试候选浏览器: 某个版本启动失败就换下一个
let browser = null;
const candidates = findChromiumCandidates();
for (const p of candidates) {
  try {
    browser = await chromium.launch({ executablePath: p });
    break;
  } catch (e) {
    process.stderr.write(`[visual_check] 浏览器启动失败, 尝试下一个: ${p}\n`);
  }
}
if (!browser) browser = await chromium.launch({});   // 兜底: 用 playwright 自带
const page = await browser.newPage({ viewport: { width: 1340, height: 900 } });
await page.goto(HTML, { waitUntil: 'load' });
await page.waitForTimeout(900);

// ---- 页面内几何自校验 (与 L1 同一套判据)
const geo = await page.evaluate(() => {
  const out = { rows: [], errs: [] };
  const S = window.SCENES || window.__SCENES__;
  if (!S) return { errs: ['页面未暴露 SCENES'], rows: [] };
  const C = S.ct;
  // 逐场景判断: 该柜是否为托盘模式 (合并多方案时同一页面可同时有无托盘柜)
  const isPalletItem = (it) => it.boxes.some(b => b.kind === 'pallet');
  for (const it of S.items) {
    let n = 0, outWall = 0, overDoor = 0, overPal = 0, mx = -1e9, my = -1e9;
    const pals = it.boxes.filter(b => b.kind === 'pallet');
    for (const b of it.boxes) {
      if (b.kind === 'pallet') continue;
      n++;
      if (b.x < -0.6 || b.y < -0.6 || b.x + b.w > C.L + 0.6 || b.y + b.d > C.W + 0.6) outWall++;
      if (b.z + b.h > C.doorH + 0.6) overDoor++;
      // 箱子必须在某个托盘面内 (仅该柜为托盘模式时; 裸地箱 bare 直接落柜底, 不参与该判定)
      if (isPalletItem(it) && b.kind !== 'bare') {
        const inside = pals.some(p => {
          const px0 = p.x, py0 = p.y, px1 = p.x + p.w, py1 = p.y + p.d;
          return b.x >= px0 - 0.6 && b.y >= py0 - 0.6 && b.x + b.w <= px1 + 0.6 && b.y + b.d <= py1 + 0.6;
        });
        if (!inside) overPal++;
      }
      mx = Math.max(mx, b.x + b.w); my = Math.max(my, b.y + b.d);
    }
    out.rows.push([it.idx, it.batch, it.cartons, n, outWall, overDoor, overPal,
                   Math.round(mx), Math.round(my)]);
    if (outWall || overDoor || overPal || n !== it.cartons) {
      out.errs.push(`柜${it.idx}: 箱数${n}/${it.cartons} 出壁${outWall} 超门高${overDoor} 出托盘${overPal}`);
    }
  }
  return out;
});

console.log('柜 | 批次 | cartons | 实际箱 | 出柜壁 | 超门高 | 出托盘 | maxX | maxY');
for (const r of geo.rows) console.log(r.join(' | '));
console.log('\n几何自校验: ' + (geo.errs.length ? '✗\n  ' + geo.errs.join('\n  ') : '✓ 全部通过'));

// ---- 逐柜截图 (按全局序号定位, 兼容合并多方案)
const ids = CH || geo.rows.map(r => r[0]);
for (const idx of ids) {
  await page.evaluate((i) => {
    const S = window.SCENES || window.__SCENES__;
    const k = S.items.findIndex(x => x.idx === i);
    if (k >= 0) {
      if (window.__setSceneByIndex) window.__setSceneByIndex(k);
      else window.__setScene(S.items[k].batch, k);
    }
  }, idx);
  await page.waitForTimeout(240);
  await page.locator('#cv').screenshot({ path: path.join(OUT, `L4_c${idx}.png`) });
}
// ---- 特写: 俯视 + 侧视, 便于目视核对分层
await page.evaluate(() => window.__setView(1.5708, 0.02, 1.2));
await page.waitForTimeout(200);
await page.locator('#cv').screenshot({ path: path.join(OUT, 'L4_topview.png') });

console.log('\n截图输出 →', OUT);
await browser.close();
process.exit(geo.errs.length ? 1 : 0);
