// Post-build step: land the single-file Vite output at the repo-root
// committed-artifact path. Uses Node fs (not `cp`) so it runs identically
// on Windows dev machines and Linux CI. No mutation of the file content —
// a byte copy — so the build stays deterministic for the freshness gate.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "..", "dist", "index.html");
const dest = resolve(here, "..", "..", "..", "assets", "dashboard.html");

if (!existsSync(src)) {
  console.error(`[copy-artifact] build output not found: ${src}`);
  process.exit(1);
}

mkdirSync(dirname(dest), { recursive: true });
copyFileSync(src, dest);
console.log(`[copy-artifact] ${src} -> ${dest}`);
