// Vendors the flag-icons stylesheet (and its flag SVGs) out of the JS/CSS bundle
// into public/.
//
// Why: flag-icons maps every country to an SVG — 542 rules, and when the CSS was
// imported from `main.tsx` Vite rewrote each url() to a hashed /assets/*.svg
// path, inflating the panel's single render-blocking stylesheet to 489 КБ
// (420 КБ of it this flag table). index.html now pulls the stylesheet with
// `media="print" onload="this.media='all'"` (non-blocking), so both the CSS and
// the SVGs live in public/.
//
// ⚠️ The package CSS points at `../flags/...` — from the web root that resolves
// to `/flags/...` only by luck of URL normalisation, so the paths are rewritten
// to absolute `/flags/...` here. The SVGs MUST be copied along, otherwise every
// flag 404s (verified after build: every url() in the emitted CSS resolves to a
// file that exists).
//
// NOTE: intentionally NOT content-hashed (index.html cannot reference a hash
// without a plugin); nginx serves both paths with max-age=86400, so a flag-set
// change can lag a day — cosmetic only.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pkg = path.join(root, 'node_modules', 'flag-icons');
const srcCss = path.join(pkg, 'css', 'flag-icons.min.css');
const srcFlags = path.join(pkg, 'flags');
const destCss = path.join(root, 'public', 'flag-icons.min.css');
const destFlags = path.join(root, 'public', 'flags');

if (!fs.existsSync(srcCss) || !fs.existsSync(srcFlags)) {
  console.error(`[vendor-flagicons] flag-icons not found under ${pkg} — run npm install first.`);
  process.exit(1);
}
fs.mkdirSync(path.dirname(destCss), { recursive: true });
const css = fs.readFileSync(srcCss, 'utf8').replace(/url\(\.\.\/flags\//g, 'url(/flags/');
fs.writeFileSync(destCss, css);

fs.rmSync(destFlags, { recursive: true, force: true });
fs.cpSync(srcFlags, destFlags, { recursive: true });

const svgs = fs.readdirSync(path.join(destFlags, '4x3')).length + fs.readdirSync(path.join(destFlags, '1x1')).length;
console.log(`[vendor-flagicons] flag-icons.min.css -> ${path.relative(root, destCss)} (${fs.statSync(destCss).size} bytes), ${svgs} SVGs -> ${path.relative(root, destFlags)}/`);
