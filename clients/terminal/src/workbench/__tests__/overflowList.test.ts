import { describe, it, expect, vi, afterEach } from "vitest";
import { dropFromOverflowList, recountOverflow } from "../overflowList";

/** The "more tabs" list dockview shows when tabs overflow: a snapshot built when it opens, one `.dv-tab` row per hidden tab. */
function list(...titles: string[]) {
  const root = document.createElement("div");
  root.className = "dv-tabs-overflow-container";
  for (const title of titles) {
    const row = document.createElement("div");
    row.className = "dv-tab";
    row.innerHTML = `<div class="dv-default-tab"><span>${title}</span><span class="dv-default-tab-action">×</span></div>`;
    root.appendChild(row);
  }
  document.body.appendChild(root);
  return root;
}
const closeButton = (root: Element, n: number) => root.querySelectorAll(".dv-default-tab-action")[n];

afterEach(() => { document.body.innerHTML = ""; });

describe("dropFromOverflowList", () => {
  it("removes the closed tab's row at once and leaves the others", () => {
    const root = list("a", "b", "c");
    dropFromOverflowList(closeButton(root, 1));
    expect([...root.querySelectorAll("span:first-child")].map((s) => s.textContent)).toEqual(["a", "c"]);
  });

  it("closes the list (Escape, which dockview listens for) when the last row goes", () => {
    const root = list("a");
    const keys: string[] = [];
    const onKey = (e: KeyboardEvent) => keys.push(e.key);
    window.addEventListener("keydown", onKey);
    dropFromOverflowList(closeButton(root, 0));
    window.removeEventListener("keydown", onKey);
    expect(keys).toEqual(["Escape"]);
  });

  it("keeps the list open while rows remain", () => {
    const root = list("a", "b");
    const onKey = vi.fn();
    window.addEventListener("keydown", onKey);
    dropFromOverflowList(closeButton(root, 0));
    window.removeEventListener("keydown", onKey);
    expect(onKey).not.toHaveBeenCalled();
  });

  it("does nothing for a tab in the tab strip, which dockview already updates itself", () => {
    const strip = document.createElement("div");
    strip.className = "dv-tab";
    strip.innerHTML = `<span class="dv-default-tab-action">×</span>`;
    document.body.appendChild(strip);
    dropFromOverflowList(strip.querySelector("span"));
    expect(document.querySelector(".dv-tab")).not.toBeNull();
    dropFromOverflowList(null);
  });
});

describe("recountOverflow", () => {
  it("nudges every tab strip to recount, after two frames", () => {
    document.body.innerHTML = `<div class="dv-tabs-container"></div><div class="dv-tabs-container"></div>`;
    const seen: number[] = [];
    document.querySelectorAll(".dv-tabs-container").forEach((el, i) => el.addEventListener("scroll", () => seen.push(i)));
    const frames: (() => void)[] = [];
    recountOverflow(document, (cb) => frames.push(cb));
    expect(seen).toEqual([]);                       // nothing yet: the closed tab may still be laying out
    frames.shift()!();                              // frame 1 schedules frame 2
    expect(seen).toEqual([]);
    frames.shift()!();
    expect(seen).toEqual([0, 1]);
  });
});
