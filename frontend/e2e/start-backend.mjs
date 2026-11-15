// Starts an ISOLATED AIMO backend for E2E tests on :8100 with a brand-new SQLite database in a temp directory.
// Never touches backend/aimo.db: the script refuses to start if the database path resolves inside backend/.
// Every external setting is overridden, so the real VALIO_API_KEY from backend/.env is never sent anywhere.
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { E2E } from "./e2e-env.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const backendDir = path.resolve(here, "../../backend");
const workDir = process.env.AIMO_E2E_DIR || path.join(os.tmpdir(), "aimo-e2e");
const dbPath = path.join(workDir, "e2e.db");

const realDb = path.join(backendDir, "aimo.db");
if (path.resolve(dbPath) === realDb || path.resolve(dbPath).startsWith(backendDir + path.sep)) {
  console.error(`Refusing to run E2E against ${dbPath}: E2E must never use the development database.`);
  process.exit(1);
}
fs.mkdirSync(workDir, { recursive: true });
for (const f of [dbPath, `${dbPath}-wal`, `${dbPath}-shm`]) fs.rmSync(f, { force: true });

const python = process.env.AIMO_BACKEND_PYTHON || path.join(backendDir, ".venv/bin/python");
const env = {
  ...process.env,
  APP_ENV: "test",
  DATABASE_URL: `sqlite:///${dbPath}`,
  AUTH_SECRET: crypto.randomBytes(48).toString("base64url"),
  DEMO_PASSWORD: E2E.demoPassword,
  SEED_DEMO_USERS: "true",
  AGENT_API_KEY: E2E.agentKey,
  VALIO_API_BASE_URL: `http://127.0.0.1:${E2E.mockValioPort}`,
  VALIO_API_KEY: E2E.mockValioKey,
  VALIO_CACHE_TTL_SECONDS: "0",
  VALIO_TIMEOUT_SECONDS: "3",
  LLM_PROVIDER: "none",
  LLM_API_KEY: "",
  CORS_ORIGINS: E2E.uiUrl,
  AUTO_INGEST: "true",
  SESSION_COOKIE_SECURE: "false",
  AGENT_RATE_LIMIT_PER_MINUTE: "1000",
};
console.log(`[e2e-backend] database: ${dbPath} (fresh)`);
const t0 = Date.now();
const child = spawn(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(E2E.apiPort)], {
  cwd: backendDir,
  env,
  stdio: ["ignore", "pipe", "pipe"],
});
const relay = (stream, out) =>
  stream.on("data", (b) => {
    const s = b.toString();
    out.write(s);
    if (s.includes("Application startup complete")) console.log(`[e2e-backend] ready after ${((Date.now() - t0) / 1000).toFixed(1)} s (includes catalogue ingestion)`);
  });
relay(child.stdout, process.stdout);
relay(child.stderr, process.stderr);
const stop = () => child.kill("SIGTERM");
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
child.on("exit", (code) => process.exit(code ?? 0));
