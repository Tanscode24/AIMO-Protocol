// Builds and serves the frontend for E2E on :3100 from a COPY in a temp directory, pointed at the isolated
// backend on :8100. Building in a copy keeps the developer's .next/ (used by a running `next dev`) and
// tsconfig.json untouched.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { E2E } from "./e2e-env.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, "..");
const dest = path.join(process.env.AIMO_E2E_DIR || path.join(os.tmpdir(), "aimo-e2e"), "frontend");
const SKIP = new Set(["node_modules", ".next", "e2e", "playwright.config.ts", "test-results", "playwright-report", "tsconfig.tsbuildinfo"]);

fs.rmSync(dest, { recursive: true, force: true });
fs.mkdirSync(dest, { recursive: true });
for (const entry of fs.readdirSync(src)) {
  if (SKIP.has(entry)) continue;
  fs.cpSync(path.join(src, entry), path.join(dest, entry), { recursive: true });
}
fs.symlinkSync(path.join(src, "node_modules"), path.join(dest, "node_modules"), "dir");

const next = path.join(src, "node_modules/.bin/next");
const env = { ...process.env, NEXT_PUBLIC_API_BASE_URL: E2E.apiUrl, NEXT_TELEMETRY_DISABLED: "1" };
const t0 = Date.now();
const build = spawnSync(next, ["build"], { cwd: dest, env, stdio: "inherit" });
if (build.status !== 0) process.exit(build.status ?? 1);
console.log(`[e2e-frontend] production build took ${((Date.now() - t0) / 1000).toFixed(1)} s`);
const child = spawn(next, ["start", "-p", String(E2E.uiPort), "-H", "localhost"], { cwd: dest, env, stdio: "inherit" });
const stop = () => child.kill("SIGTERM");
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
child.on("exit", (code) => process.exit(code ?? 0));
