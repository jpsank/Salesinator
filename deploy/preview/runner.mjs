// The preview runner: asks sales-cycle which of the agent's pull requests want a live preview, builds each one with
// preview.sh, and reports how it went (sales-cycle says it in the card's Slack thread). One at a time, on this machine.
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const exec = promisify(execFile);
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");

/** A change is worth previewing when it touches the terminal — the only thing a preview can show today. */
export const touchesTerminal = (files) => files.some((f) => f.startsWith("clients/terminal/"));

export function previewUrl({ domain, port }, pr) {
  return domain === "localhost" ? `http://preview-pr-${pr}.localhost:${port}` : `https://preview-pr-${pr}.${domain}`;
}

/** Handle one wanted pull request. `sh(cmd, args)` runs a command and resolves with its stdout, or rejects. */
export async function handle(item, { sh, report, remote, ...site }) {
  const head = `refs/previews/pr-${item.pr}`;
  const merge = `${head}-merge`;
  try {
    await sh("git", ["fetch", "--quiet", remote, `+pull/${item.pr}/head:${head}`]);
    // GitHub's merge ref names the change exactly: its first parent is the base the pull request was opened against.
    let files = null;
    try {
      await sh("git", ["fetch", "--quiet", remote, `+pull/${item.pr}/merge:${merge}`]);
      files = (await sh("git", ["diff", "--name-only", `${merge}^1`, merge])).split("\n").filter(Boolean);
    } catch {
      files = null; // no merge ref (conflicts): assume the terminal changed rather than hide a preview
    }
    if (files && !touchesTerminal(files)) return report(item.id, { state: "skipped" });
    await sh(join(HERE, "preview.sh"), ["up", String(item.pr), head]);
    return report(item.id, { state: "ready", url: previewUrl(site, item.pr) });
  } catch (err) {
    console.error(`[preview-runner] pr-${item.pr} failed: ${String(err.stderr || err.message).slice(-400)}`);
    return report(item.id, { state: "failed" });
  }
}

export async function runOnce(deps) {
  const wanted = await deps.fetchWanted();
  for (const item of wanted) await handle(item, deps);
  return wanted.length;
}

function setting(text, key) {
  return (text.match(new RegExp(`^${key}=(.*)$`, "m")) || [])[1] || "";
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const state = process.env.PREVIEW_STATE || join(homedir(), "vexa-data", "preview");
  const gateEnv = readFileSync(join(state, "gate.env"), "utf8");
  // Only the one value the runner needs is read from the stack's .env.
  const composeEnv = readFileSync(join(ROOT, "deploy", "compose", ".env"), "utf8");
  const secret = setting(composeEnv, "INTERNAL_API_SECRET");
  const base = (process.env.SALES_CYCLE_URL || `http://127.0.0.1:${setting(composeEnv, "SALES_CYCLE_PORT") || 18300}`).replace(/\/$/, "");
  const call = (path, init = {}) =>
    fetch(`${base}${path}`, { ...init, headers: { "X-Internal-Secret": secret, "Content-Type": "application/json" }, signal: AbortSignal.timeout(15000) });
  const deps = {
    sh: async (cmd, args) => (await exec(cmd, args, { cwd: ROOT, maxBuffer: 16 * 1024 * 1024 })).stdout,
    remote: process.env.PREVIEW_GIT_REMOTE || "salesinator",
    domain: setting(gateEnv, "PREVIEW_DOMAIN") || "localhost",
    port: process.env.PREVIEW_PORT || "13100",
    fetchWanted: async () => {
      const res = await call("/internal/previews/wanted");
      if (!res.ok) throw new Error(`sales-cycle returned ${res.status}`);
      return res.json();
    },
    report: (id, body) => call(`/internal/previews/${id}`, { method: "POST", body: JSON.stringify(body) }),
  };
  const every = Number(process.env.PREVIEW_POLL_SECONDS || 30) * 1000;
  console.log(`[preview-runner] watching ${base} every ${every / 1000}s`);
  for (;;) {
    try {
      await runOnce(deps);
    } catch (err) {
      console.error(`[preview-runner] ${err.message}`);
    }
    await new Promise((r) => setTimeout(r, every));
  }
}
