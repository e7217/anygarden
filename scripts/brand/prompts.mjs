#!/usr/bin/env node
// Image-generation prompts (ChatGPT Images, Gemini, Midjourney…) for Anygarden
// logos, brand boards and banners. Structure follows the brandkit skill
// (Leonxlnx/imagegen-skills): strategy first, one concept method, sparse text.
//
//   node scripts/brand/prompts.mjs                     # all prompts → stdout
//   node scripts/brand/prompts.mjs --only logo,banner  # some of them
//   node scripts/brand/prompts.mjs --mark grid --mode light --out .tmp/brand-prompts.md
import { writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    only: { type: 'string', default: 'concepts,logo,board,social,readme' },
    mark: { type: 'string', default: 'monogram' },
    mode: { type: 'string', default: 'dark' },
    tagline: { type: 'string', default: 'One room. Any agent.' },
    out: { type: 'string' },
    help: { type: 'boolean', short: 'h', default: false },
  },
});

const BRAND = {
  name: 'anygarden',
  category: 'open-source, self-hosted workspace where people and AI coding agents (Claude Code, Codex, Pi) work together in chat rooms',
  audience: 'software developers and small teams who already use AI coding agents',
  personality: 'calm, precise, collaborative, quietly confident, builder-native — never hype',
  metaphor: 'a tended garden: different agents grow side by side in one shared plot, and people tend it',
  command: 'pip install anygarden',
  url: 'github.com/e7217/anygarden',
  // Matches packages/cluster/frontend/src/index.css
  palette: {
    dark: 'near-black green #0B1514 base, deep teal #0F766E primary, mint #5EEAD4 accent, fog #E7F2F0 text',
    light: 'fog #F1F6F5 base, deep teal #0F766E primary, ink #0F1F1D text, pale mint #DDE9E7 surfaces',
  },
  type: 'geometric grotesque sans (Inter-like) for the lowercase wordmark, a monospace for commands',
};

// One concept method each, per the brandkit "LOGO CONCEPT METHODS".
const MARKS = {
  monogram: {
    label: 'Monogram + meaning',
    idea: 'a single-storey lowercase "a" drawn with one even stroke weight; the top of its vertical stem sprouts a single leaf pointing up-right. Round terminals, no gradient, reads at 16 px',
  },
  grid: {
    label: 'Construction geometry',
    idea: 'a 2×2 plot of four different solid shapes — circle, rounded square, quarter circle, diamond — sharing one baseline grid, like different plants in one garden bed; each shape stands for a different agent',
  },
  sprout: {
    label: 'Metaphor fusion',
    idea: 'a minimal sprout whose two leaves are shaped like speech bubbles growing in opposite directions — a person and an agent in conversation',
  },
  network: {
    label: 'Negative space',
    idea: 'a five-petal flower where each petal is a hollow ring joined to a solid centre by a thin line — participants around one room',
  },
};

const AVOID =
  'Avoid: gradients, glow, neon, 3D, sparkles, robots, brains, circuit boards, generic AI purple-blue, clip-art leaves, mascots, tiny fake text, lorem ipsum, copied famous logos.';

function strategy(mark) {
  return `Brand strategy:
- identity mode: create from scratch
- brand name: "${BRAND.name}" (always lowercase)
- category: ${BRAND.category}
- audience: ${BRAND.audience}
- personality: ${BRAND.personality}
- core metaphor: ${BRAND.metaphor}
- logo concept (${mark.label}): ${mark.idea}`;
}

const PROMPTS = {
  concepts: {
    title: '1. 로고 콘셉트 시트 (방향 탐색)',
    note: '먼저 이것으로 방향을 고릅니다. 정사각 1024×1024.',
    body: () => `Create a logo concept sheet for "${BRAND.name}".

${strategy(MARKS[args.mark])}

Canvas: square 1:1.
Layout: 2×2 grid on a plain white background with generous gutters. Each cell shows one different logo concept as a flat solid mark in deep teal #0F766E, with the lowercase wordmark "${BRAND.name}" small underneath.
The four concepts:
1. ${MARKS.monogram.idea}
2. ${MARKS.grid.idea}
3. ${MARKS.sprout.idea}
4. ${MARKS.network.idea}

Style: flat vector, geometric construction, consistent stroke weights, crisp edges, lots of negative space, professional identity-studio quality. Every mark must stay recognisable at favicon size.
${AVOID}`,
  },
  logo: {
    title: '2. 선택한 로고 다듬기 (사용 크기별)',
    note: `--mark 로 고른 콘셉트(${args.mark})를 다듬습니다. 결과는 SVG로 다시 그려야 favicon에 쓸 수 있습니다.`,
    body: () => `Refine one logo for "${BRAND.name}".

${strategy(MARKS[args.mark])}

Canvas: 3:2 landscape, plain white background.
Show, left to right in one row with even spacing:
1. the mark large, with faint construction circles and grid lines behind it showing its geometry
2. the horizontal lockup: mark + lowercase wordmark "${BRAND.name}" in a geometric grotesque sans, bold, tight tracking
3. an app icon: white mark on a deep teal #0F766E rounded square
4. the mark at 32 px and 16 px, still legible
5. the mark reversed in mint #5EEAD4 on near-black #0B1514

Each mark in one flat colour from the teal family only, flat vector, no shading. Small labels only: "mark", "lockup", "app icon", "favicon".
${AVOID}`,
  },
  board: {
    title: '3. 브랜드 키트 보드 (3×3)',
    note: '브랜드 전체 분위기 확인용. 16:10 가로.',
    body: () => `Create a premium brand-kit overview image for "${BRAND.name}".

${strategy(MARKS[args.mark])}

Canvas: 16:10 landscape.
Layout: 3×3 grid on a ${args.mode === 'dark' ? 'dark charcoal' : 'warm off-white'} presentation canvas with strong, even gutters and refined negative space.

Panels:
1. Logo cover — large mark and lowercase wordmark, nothing else
2. Logo construction — the mark on a geometric grid with construction circles
3. Digital application — a terminal window showing "${BRAND.command}"
4. Brand essence — the tagline "${args.tagline}" in large type
5. Color system — swatches: ${BRAND.palette[args.mode]}
6. Typography — large "Aa" specimen of the grotesque sans plus a monospace line
7. Physical application — a matte sticker sheet or enamel pin with the mark
8. Image direction — misty garden rows at dawn with a subtle halftone texture, teal-tinted
9. System detail — a chat input bar with an "@agent" mention chip and small agent avatars made from the logo shapes

Visual mode: Dark Nature / Calm System crossed with Dark Developer / Builder — calm, precise, builder-native.
Palette: ${BRAND.palette[args.mode]}. One accent carries the system.
Typography: readable, minimal, high hierarchy, no tiny fake text. Only these words may appear: "${BRAND.name}", "${args.tagline}", "${BRAND.command}", "@agent", "Aa".
Logo: repeat the same mark consistently across panels.
${AVOID}`,
  },
  social: {
    title: '4. GitHub social preview 배너',
    note: 'GitHub 규격은 1280×640(2:1). ChatGPT는 3:2(1536×1024)로 받아 위아래를 잘라 쓰세요.',
    body: () => `Create a GitHub social preview banner for "${BRAND.name}".

${strategy(MARKS[args.mark])}

Canvas: 3:2 landscape; keep all content inside the central 2:1 band so the top and bottom can be cropped to 1280×640.
Composition: left 60% holds the mark followed by the large lowercase wordmark "${BRAND.name}", below it the tagline "${args.tagline}", below that a monospace chip "${BRAND.command}". Right 40% holds a quiet arrangement of the logo's geometric shapes like plants in a garden bed, some at reduced opacity.
Background: ${args.mode === 'dark' ? 'near-black green #0B1514 with mint #5EEAD4 and teal #0F766E accents' : 'fog #F1F6F5 with deep teal #0F766E accents and ink #0F1F1D text'}.
Style: flat vector, sparse, generous margins, large readable text, no other words.
${AVOID}`,
  },
  readme: {
    title: '5. README 헤더 (가로 띠)',
    note: '4:1 띠가 필요하므로 3:2로 생성한 뒤 가운데를 잘라 쓰세요.',
    body: () => `Create a wide README header for "${BRAND.name}".

Canvas: 3:2 landscape; place all content in a thin horizontal band across the vertical centre (about one quarter of the height) so it can be cropped to 4:1. Everything outside the band is plain background.
Inside the band: the logo mark (${MARKS[args.mark].idea}) on the left, then the lowercase wordmark "${BRAND.name}" large and bold, then the tagline "${args.tagline}" smaller beside or below it.
Background: ${args.mode === 'dark' ? 'near-black green #0B1514; mark in mint #5EEAD4; text in fog #E7F2F0' : 'fog #F1F6F5; mark in deep teal #0F766E; text in ink #0F1F1D'}.
Style: flat vector, calm, precise, no decoration beyond the mark.
${AVOID}`,
  },
};

function main() {
  if (args.help) {
    console.log(`Usage: node scripts/brand/prompts.mjs [--only ${Object.keys(PROMPTS).join(',')}] [--mark ${Object.keys(MARKS).join('|')}] [--mode dark|light] [--tagline TEXT] [--out FILE]`);
    return;
  }
  if (!MARKS[args.mark]) throw new Error(`unknown --mark "${args.mark}" (choose ${Object.keys(MARKS).join(', ')})`);
  if (!BRAND.palette[args.mode]) throw new Error(`unknown --mode "${args.mode}" (dark or light)`);
  const keys = args.only.split(',').map((k) => k.trim()).filter(Boolean);
  const unknown = keys.filter((k) => !PROMPTS[k]);
  if (unknown.length) throw new Error(`unknown --only ${unknown.join(', ')} (choose ${Object.keys(PROMPTS).join(', ')})`);

  const md = [
    `# ${BRAND.name} 이미지 생성 프롬프트`,
    `mark: ${args.mark} (${MARKS[args.mark].label}) · mode: ${args.mode} · tagline: "${args.tagline}"`,
    '',
    '프롬프트는 영어로 넣는 편이 결과가 안정적입니다. 결과는 래스터라 최종 로고·favicon은 SVG로 다시 그려야 합니다.',
    ...keys.flatMap((k) => ['', `## ${PROMPTS[k].title}`, `> ${PROMPTS[k].note}`, '', '```text', PROMPTS[k].body(), '```']),
    '',
  ].join('\n');

  if (args.out) return writeFile(args.out, md).then(() => console.log(`wrote ${args.out}`));
  process.stdout.write(md);
}

Promise.resolve()
  .then(main)
  .catch((err) => {
    console.error(err.message ?? err);
    process.exit(1);
  });
