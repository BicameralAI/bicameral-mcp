import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// The build emits ONE self-contained file to dist/index.html (all JS/CSS
// inlined by vite-plugin-singlefile). scripts/copy-artifact.mjs then lands
// it at ../../assets/dashboard.html. The build is intentionally free of
// timestamp/banner injection so the committed-artifact freshness gate in
// CI (git diff --exit-code) stays byte-deterministic.
export default defineConfig({
  plugins: [preact(), viteSingleFile()],
  build: {
    target: "es2022",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    reportCompressedSize: false,
    chunkSizeWarningLimit: 100_000,
  },
  test: {
    environment: "happy-dom",
    globals: true,
    include: ["src/**/*.test.tsx"],
  },
});
