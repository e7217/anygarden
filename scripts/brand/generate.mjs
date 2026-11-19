#!/usr/bin/env node
// Anygarden brand assets: symbol, lockups, favicon set and banners.
//
//   node scripts/brand/generate.mjs                 # mark D → .tmp/brand/
//   node scripts/brand/generate.mjs --mark a --out .tmp/brand-a
//   node scripts/brand/generate.mjs --tagline "Your agents, one room."
//
// SVGs are written as text; PNG/ICO are rendered with the repo's Playwright
// Chromium. Wordmarks load Inter from Google Fonts, so PNGs need network for
// the exact face. SVG lockups keep the wordmark as <text> (Inter stack) — they
// render correctly only where Inter is available.
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';

const DEFAULT_TAGLINE = 'A shared workspace where people and AI coding agents work as a team.';

const { values: args } = parseArgs({
  options: {
    mark: { type: 'string', default: 'd' },
    out: { type: 'string', default: '.tmp/brand' },
    name: { type: 'string', default: 'anygarden' },
    tagline: { type: 'string', default: DEFAULT_TAGLINE },
    install: { type: 'string', default: 'pip install anygarden' },
    help: { type: 'boolean', short: 'h', default: false },
  },
});

// Colours follow packages/cluster/frontend/src/index.css (--color-brand etc.).
const THEMES = {
  light: { ground: '#f1f6f5', ink: '#0f1f1d', muted: '#4b5c5a', chip: '#dde9e7', accent: '#0f766e', onAccent: '#ffffff' },
  dark: { ground: '#0b1514', ink: '#e7f2f0', muted: '#9fb5b1', chip: '#16302d', accent: '#5eead4', onAccent: '#0b1514' },
};

const FONT_STACK = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif";
const FONT_LINK =
  '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap">';

// ── Marks ──────────────────────────────────────────────────────────────────
// Each draw(p, px) returns SVG children in a 64×64 box. `p.f` / `p.s` are the
// fill / stroke attributes (a colour or a class), `p.bg` the ground colour when
// known. `px` is the rendered size so strokes can thicken for small icons.
const MARKS = {
  a: {
    label: 'Sprout',
    draw: (p, px) => {
      const w = px <= 16 ? 8 : px <= 32 ? 6 : 5;
      return (
        `<path d="M32 58V34" ${p.s} stroke-width="${w}" stroke-linecap="round" fill="none"/>` +
        `<path d="M31 38C18 39 9 31 10 18C23 17 31 25 31 38Z" ${p.f}/>` +
        `<path d="M33 32C34 19 43 11 55 12C55 25 46 32 33 32Z" ${p.f}/>`
      );
    },
  },
  b: {
    label: 'Garden grid',
    draw: (p) =>
      `<circle cx="18" cy="18" r="10" ${p.f}/>` +
      `<rect x="36" y="8" width="20" height="20" rx="5" ${p.f}/>` +
      `<path d="M8 56A20 20 0 0 1 28 36V56Z" ${p.f}/>` +
      `<path d="M46 35L57 46L46 57L35 46Z" ${p.f}/>`,
  },
  c: {
    label: 'Flower network',
    draw: (p, px) => {
      const petals = [[32, 11], [52, 25.5], [44.4, 49], [19.6, 49], [12, 25.5]];
      if (px <= 16) {
        return `<circle cx="32" cy="32" r="9" ${p.f}/>` + petals.map(([x, y]) => `<circle cx="${x}" cy="${y}" r="8" ${p.f}/>`).join('');
      }
      const spokes = petals
        .map(([x, y]) => `<path d="M32 32L${x} ${y}" ${p.s} stroke-width="${px <= 32 ? 4 : 3}" stroke-linecap="round"/>`)
        .join('');
      const hollow = px > 32 && p.bg;
      const ring = hollow ? `fill="${p.bg}" ${p.s} stroke-width="4"` : p.f;
      return spokes + `<circle cx="32" cy="32" r="8" ${p.f}/>` + petals.map(([x, y]) => `<circle cx="${x}" cy="${y}" r="${hollow ? 6 : 7}" ${ring}/>`).join('');
    },
  },
  d: {
    label: 'a + leaf monogram',
    draw: (p, px) => {
      const w = px <= 16 ? 10 : px <= 32 ? 8 : 7;
      return (
        `<circle cx="25" cy="40" r="13" ${p.s} stroke-width="${w}" fill="none"/>` +
        `<path d="M38 28V53" ${p.s} stroke-width="${w}" stroke-linecap="round" fill="none"/>` +
        `<path d="M38 28C38 15 46 8 57 8C57 20 50 28 38 28Z" ${p.f}/>`
      );
    },
  },
};

const solid = (color, bg) => ({ f: `fill="${color}"`, s: `stroke="${color}"`, bg });
const byClass = { f: 'class="f"', s: 'class="s"' };

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function svgDoc(children, { w = 64, h = 64, viewBox = `0 0 ${w} ${h}`, title = args.name, head = '' } = {}) {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" width="${w}" height="${h}" role="img" aria-label="${esc(title)}">` +
    `<title>${esc(title)}</title>${head}${children}</svg>\n`
  );
}

// ── Lockups (need text metrics, so built after the font loads) ──────────────
const WORD = { size: 44, weight: 700, tracking: -0.04 };

function horizontalLockup(mark, theme, wordWidth) {
  const t = THEMES[theme];
  const gap = 6;
  const w = Math.ceil(64 + gap + wordWidth + 4);
  const text = `<text x="${64 + gap}" y="46" font-family="${FONT_STACK}" font-size="${WORD.size}" font-weight="${WORD.weight}" letter-spacing="${WORD.tracking * WORD.size}" fill="${t.ink}">${esc(args.name)}</text>`;
  return { w, h: 64, svg: svgDoc(mark.draw(solid(t.accent, t.ground), 64) + text, { w, h: 64 }) };
}

function verticalLockup(mark, theme, wordWidth) {
  const t = THEMES[theme];
  const w = Math.ceil(Math.max(96, wordWidth) + 8);
  const h = 96 + 16 + 44;
  const markG = `<g transform="translate(${(w - 96) / 2} 0) scale(1.5)">${mark.draw(solid(t.accent, t.ground), 96)}</g>`;
  const text = `<text x="${w / 2}" y="${96 + 16 + 34}" text-anchor="middle" font-family="${FONT_STACK}" font-size="${WORD.size}" font-weight="${WORD.weight}" letter-spacing="${WORD.tracking * WORD.size}" fill="${t.ink}">${esc(args.name)}</text>`;
  return { w, h, svg: svgDoc(markG + text, { w, h }) };
}

// ── Banners (HTML rendered to PNG) ─────────────────────────────────────────
function decoration(accent) {
  return `<svg width="360" height="440" viewBox="0 0 360 440" aria-hidden="true"><g fill="${accent}">
<circle cx="70" cy="70" r="46"/><rect x="160" y="24" width="92" height="92" rx="22" opacity="0.35"/>
<path d="M24 296A92 92 0 0 1 116 204V296Z" opacity="0.55"/><path d="M226 160L296 230L226 300L156 230Z"/>
<circle cx="300" cy="96" r="22" opacity="0.55"/><rect x="24" y="336" width="92" height="92" rx="46" opacity="0.2"/>
<path d="M200 428C200 372 236 340 300 340C300 396 264 428 200 428Z" opacity="0.8"/></g></svg>`;
}

function markInline(mark, t, px) {
  return `<svg width="${px}" height="${px}" viewBox="0 0 64 64" aria-hidden="true">${mark.draw(solid(t.accent, t.ground), px)}</svg>`;
}

function socialBanner(mark, theme) {
  const t = THEMES[theme];
  return `<div style="width:1280px;height:640px;box-sizing:border-box;padding:88px 96px;background:${t.ground};color:${t.ink};font-family:${FONT_STACK};display:flex;align-items:center;justify-content:space-between;gap:48px;overflow:hidden">
<div style="display:flex;flex-direction:column;gap:32px;max-width:660px">
<div style="display:flex;align-items:center;gap:24px">${markInline(mark, t, 104)}<span style="font-size:80px;font-weight:700;letter-spacing:-0.04em;line-height:1">${esc(args.name)}</span></div>
<p style="margin:0;font-size:32px;line-height:1.3;font-weight:500;letter-spacing:-0.01em">${esc(args.tagline)}</p>
<div><span style="display:inline-block;padding:10px 18px;border-radius:10px;background:${t.chip};font-family:'JetBrains Mono',monospace;font-size:20px">${esc(args.install)}</span></div>
</div>${decoration(t.accent)}</div>`;
}

function readmeHeader(mark, theme) {
  const t = THEMES[theme];
  return `<div style="width:1280px;height:320px;box-sizing:border-box;padding:0 80px;background:${t.ground};color:${t.ink};font-family:${FONT_STACK};display:flex;align-items:center;gap:40px;border-radius:24px;overflow:hidden">
${markInline(mark, t, 136)}
<div style="display:flex;flex-direction:column;gap:14px">
<span style="font-size:72px;font-weight:700;letter-spacing:-0.04em;line-height:1">${esc(args.name)}</span>
<span style="font-size:24px;font-weight:500;color:${t.muted};line-height:1.35;white-space:nowrap">${esc(args.tagline)}</span>
</div></div>`;
}

// App icon: mark on a solid accent tile (apple-touch / PWA / maskable).
function appIcon(mark, px) {
  const t = THEMES.light;
  const inner = Math.round(px * 0.62);
  const off = (px - inner) / 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${px}" height="${px}" viewBox="0 0 ${px} ${px}"><rect width="${px}" height="${px}" fill="${t.accent}"/>` +
    `<g transform="translate(${off} ${off}) scale(${inner / 64})">${mark.draw(solid(t.onAccent, t.accent), inner)}</g></svg>`;
}

// ICO containing PNG frames (supported by every current browser and Windows Vista+).
function ico(frames) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(frames.length, 4);
  let offset = 6 + 16 * frames.length;
  const entries = frames.map(({ size, buf }) => {
    const e = Buffer.alloc(16);
    e.writeUInt8(size >= 256 ? 0 : size, 0);
    e.writeUInt8(size >= 256 ? 0 : size, 1);
    e.writeUInt16LE(1, 4);
    e.writeUInt16LE(32, 6);
    e.writeUInt32LE(buf.length, 8);
    e.writeUInt32LE(offset, 12);
    offset += buf.length;
    return e;
  });
  return Buffer.concat([header, ...entries, ...frames.map((f) => f.buf)]);
}

function previewPage(files, mark) {
  const tile = (f, bg) =>
    `<figure style="margin:0;display:flex;flex-direction:column;gap:8px"><div style="background:${bg};border-radius:12px;padding:24px;display:flex;align-items:center;justify-content:center;min-height:96px"><img src="${f}" alt="${f}" style="max-width:100%;height:auto"></div><figcaption style="font:13px/1.4 ${FONT_STACK};color:#4b5c5a">${f}</figcaption></figure>`;
  const grid = (items) => `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px">${items.join('')}</div>`;
  const icons = files.filter((f) => /\.(svg|png)$/.test(f) && !f.startsWith('banner') && !f.startsWith('readme'));
  const banners = files.filter((f) => /^(banner|readme).*\.png$/.test(f));
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(args.name)} brand assets</title>${FONT_LINK}</head>
<body style="margin:0;padding:40px;background:#ffffff;font-family:${FONT_STACK};color:#0f1f1d;display:flex;flex-direction:column;gap:32px">
<h1 style="margin:0;font-size:28px">${esc(args.name)} brand assets — mark ${args.mark.toUpperCase()} (${mark.label})</h1>
<h2 style="margin:0;font-size:18px">Marks, lockups &amp; icons</h2>${grid(icons.map((f) => tile(f, /dark|mono-light/.test(f) ? THEMES.dark.ground : THEMES.light.ground)))}
<h2 style="margin:0;font-size:18px">Banners</h2><div style="display:flex;flex-direction:column;gap:20px">${banners.map((f) => `<figure style="margin:0"><img src="${f}" alt="${f}" style="width:100%;max-width:1280px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.12)"><figcaption style="font-size:13px;color:#4b5c5a;margin-top:6px">${f}</figcaption></figure>`).join('')}</div>
</body></html>`;
}

// ── Main ───────────────────────────────────────────────────────────────────
async function main() {
  if (args.help) {
    console.log(`Usage: node scripts/brand/generate.mjs [--mark a|b|c|d] [--out DIR] [--name TEXT] [--tagline TEXT] [--install TEXT]\n\nMarks: ${Object.entries(MARKS).map(([k, m]) => `${k}=${m.label}`).join(', ')}`);
    return;
  }
  const mark = MARKS[args.mark.toLowerCase()];
  if (!mark) throw new Error(`unknown --mark "${args.mark}" (choose ${Object.keys(MARKS).join(', ')})`);
  const out = path.resolve(args.out);
  await mkdir(out, { recursive: true });
  const written = [];
  const save = async (name, data) => {
    await writeFile(path.join(out, name), data);
    written.push(name);
  };

  // Static SVGs
  await save('mark.svg', svgDoc(mark.draw(solid(THEMES.light.accent, THEMES.light.ground), 64)));
  await save('mark-dark.svg', svgDoc(mark.draw(solid(THEMES.dark.accent, THEMES.dark.ground), 64)));
  await save('mark-mono-dark.svg', svgDoc(mark.draw(solid(THEMES.light.ink, '#ffffff'), 64)));
  await save('mark-mono-light.svg', svgDoc(mark.draw(solid('#ffffff', THEMES.dark.ground), 64)));
  const faviconStyle = `<style>.f{fill:${THEMES.light.accent}}.s{stroke:${THEMES.light.accent}}@media (prefers-color-scheme:dark){.f{fill:${THEMES.dark.accent}}.s{stroke:${THEMES.dark.accent}}}</style>`;
  await save('favicon.svg', svgDoc(mark.draw(byClass, 32), { w: 32, h: 32, viewBox: '0 0 64 64', head: faviconStyle }));

  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ deviceScaleFactor: 1 });
    const load = async (body, w, h) => {
      await page.setViewportSize({ width: w, height: h });
      await page.setContent(`<!doctype html><html><head><meta charset="utf-8">${FONT_LINK}<style>html,body{margin:0;background:transparent}</style></head><body>${body}</body></html>`, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => {});
      await page.evaluate(() => document.fonts.ready);
    };
    const shot = (w, h, transparent = true) => page.screenshot({ clip: { x: 0, y: 0, width: w, height: h }, omitBackground: transparent, type: 'png' });

    await load('<span style="font:700 16px Inter">a</span>', 64, 64);
    const hasInter = await page.evaluate(() => document.fonts.check('700 16px Inter'));
    if (!hasInter) console.warn('warning: Inter did not load (offline?) — PNG wordmarks use a fallback face');
    const wordWidth = await page.evaluate(([size, weight, tracking, text]) => {
      const ctx = document.createElement('canvas').getContext('2d');
      ctx.font = `${weight} ${size}px Inter, sans-serif`;
      ctx.letterSpacing = `${tracking * size}px`;
      return ctx.measureText(text).width;
    }, [WORD.size, WORD.weight, WORD.tracking, args.name]);

    // Lockups: SVG + 2× PNG
    for (const [kind, build] of [['horizontal', horizontalLockup], ['vertical', verticalLockup]]) {
      for (const theme of ['light', 'dark']) {
        const { w, h, svg } = build(mark, theme, wordWidth);
        const base = `lockup-${kind}${theme === 'dark' ? '-dark' : ''}`;
        await save(`${base}.svg`, svg);
        await load(`<div style="width:${w * 2}px;height:${h * 2}px">${svg.replace(`width="${w}" height="${h}"`, `width="${w * 2}" height="${h * 2}"`)}</div>`, w * 2, h * 2);
        await save(`${base}@2x.png`, await shot(w * 2, h * 2));
      }
    }

    // Favicon PNG frames → favicon.ico, plus app icons
    const frames = [];
    for (const size of [16, 32, 48]) {
      await load(svgDoc(mark.draw(solid(THEMES.light.accent), size), { w: size, h: size, viewBox: '0 0 64 64' }), size, size);
      frames.push({ size, buf: await shot(size, size) });
    }
    await save('favicon.ico', ico(frames));
    for (const [name, px] of [['apple-touch-icon.png', 180], ['icon-192.png', 192], ['icon-512.png', 512]]) {
      await load(appIcon(mark, px), px, px);
      await save(name, await shot(px, px, false));
    }

    // Banners
    for (const theme of ['light', 'dark']) {
      const suffix = theme === 'dark' ? '-dark' : '';
      await load(socialBanner(mark, theme), 1280, 640);
      await save(`banner-social${suffix}.png`, await shot(1280, 640, false));
      await load(readmeHeader(mark, theme), 1280, 320);
      await save(`readme-header${suffix}.png`, await shot(1280, 320));
    }
  } finally {
    await browser.close();
  }

  await save('index.html', previewPage(written.slice(), mark));
  console.log(`mark ${args.mark.toUpperCase()} (${mark.label}) → ${path.relative(process.cwd(), out) || '.'}/`);
  for (const f of written) console.log(`  ${f}`);
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
