import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { handle, previewUrl, touchesTerminal } from "./runner.mjs";

const site = { remote: "salesinator", domain: "example.com", port: "13100" };
const run = async (item, answers) => {
  const calls = [];
  const reports = [];
  const sh = async (cmd, args) => {
    calls.push([cmd.split("/").pop(), ...args]);
    const hit = answers(cmd.split("/").pop(), args);
    if (hit instanceof Error) throw hit;
    return hit ?? "";
  };
  await handle(item, { sh, report: async (id, body) => reports.push([id, body]), ...site });
  return { calls, reports };
};

describe("preview runner", () => {
  it("knows what a preview can show", () => {
    assert.equal(touchesTerminal(["clients/terminal/src/a.tsx"]), true);
    assert.equal(touchesTerminal(["packages/transcript-rendering/x.ts", "core/a.py"]), false);
  });
  it("builds the URL for a public domain and for local use", () => {
    assert.equal(previewUrl({ domain: "example.com", port: "1" }, 7), "https://preview-pr-7.example.com");
    assert.equal(previewUrl({ domain: "localhost", port: "13100" }, 7), "http://preview-pr-7.localhost:13100");
  });
  it("builds and reports ready when the change touches the terminal", async () => {
    const { calls, reports } = await run({ id: 4, pr: 7 }, (c, a) => (c === "git" && a[0] === "diff" ? "clients/terminal/src/x.tsx\ncore/y.py\n" : ""));
    assert.deepEqual(calls.at(-1), ["preview.sh", "up", "7", "refs/previews/pr-7"]);
    assert.deepEqual(reports, [[4, { state: "ready", url: "https://preview-pr-7.example.com" }]]);
  });
  it("skips, without building, a change that touches nothing a preview can show", async () => {
    const { calls, reports } = await run({ id: 4, pr: 7 }, (c, a) => (c === "git" && a[0] === "diff" ? "packages/transcript-rendering/x.ts\n" : ""));
    assert.ok(!calls.some(([c]) => c === "preview.sh"));
    assert.deepEqual(reports, [[4, { state: "skipped" }]]);
  });
  it("builds anyway when GitHub has no merge ref to compare", async () => {
    const { reports } = await run({ id: 4, pr: 7 }, (c, a) => (c === "git" && a[1] === "--quiet" && a[3].includes("/merge") ? new Error("no ref") : ""));
    assert.equal(reports[0][1].state, "ready");
  });
  it("reports failed when the build fails", async () => {
    const { reports } = await run({ id: 4, pr: 7 }, (c) => (c === "preview.sh" ? Object.assign(new Error("boom"), { stderr: "boom" }) : "clients/terminal/a\n"));
    assert.deepEqual(reports, [[4, { state: "failed" }]]);
  });
});
