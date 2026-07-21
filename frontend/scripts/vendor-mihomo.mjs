// Vendors js-yaml into the embedded Mihomo configurator (public/mihomo/).
// The Mihomo app (public/mihomo/index.html) is a self-contained vanilla app we
// embed in an iframe; upstream it loaded js-yaml from a CDN, which our
// CSP-self-contained rule forbids. This copies the UMD build from node_modules
// (installed as a normal dependency) into public/mihomo/vendor/ so index.html
// can load it locally. Runs before `vite build`/`vite` (see package.json).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = path.join(root, 'node_modules', 'js-yaml', 'dist', 'js-yaml.min.js');
const destDir = path.join(root, 'public', 'mihomo', 'vendor');
const dest = path.join(destDir, 'js-yaml.min.js');

if (!fs.existsSync(src)) {
  console.error(`[vendor-mihomo] js-yaml not found at ${src} — run npm install first.`);
  process.exit(1);
}
fs.mkdirSync(destDir, { recursive: true });
fs.copyFileSync(src, dest);
console.log(`[vendor-mihomo] js-yaml.min.js -> ${path.relative(root, dest)}`);
