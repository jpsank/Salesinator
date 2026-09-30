/** LayoutService single-pane (mobile) tier behavior.
 *
 *  Reproduced live: the header's "Toggle left"/"Toggle right" buttons and ChatHeader's close (X)
 *  button all call layout.toggleLeft()/toggleRight() (or, for two chat.tsx listeners, read
 *  rightCollapsed directly) — flags that only drive visibility at the "narrow"/"full" width
 *  tiers. At single-pane (mobile) width, Workbench renders from `mobileFocus` instead, so every
 *  one of those callers looked broken on a phone: the flag flipped, but nothing the single-pane
 *  renderer reads ever changed. Fixed by making toggleLeft/toggleRight/showLeft/showRight
 *  themselves tier-aware (via setTier, pushed in by Workbench), so every caller — present and
 *  future — gets correct mobile behavior without needing its own tier check.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { createLayoutService, type LayoutService } from "../layout";

describe("layout service — single-pane tier", () => {
  let layout: LayoutService;

  beforeEach(() => {
    layout = createLayoutService("meetings");
  });

  it("defaults to the full tier, where toggleLeft/toggleRight flip the desktop flags", () => {
    expect(layout.store.getState().tier).toBe("full");
    layout.toggleLeft();
    expect(layout.store.getState().leftCollapsed).toBe(true);
    expect(layout.store.getState().mobileFocus).toBeNull();
  });

  it("toggleLeft/toggleRight drive mobileFocus instead, once tier is single", () => {
    layout.setTier("single");

    layout.toggleLeft();
    expect(layout.store.getState().mobileFocus).toBe("left");
    expect(layout.store.getState().leftCollapsed).toBe(false); // untouched — not what mobile reads

    // tapping the SAME toggle again returns to automatic (null), not a hidden state
    layout.toggleLeft();
    expect(layout.store.getState().mobileFocus).toBeNull();

    layout.toggleRight();
    expect(layout.store.getState().mobileFocus).toBe("right");

    // tapping the OTHER toggle switches focus directly (exclusive, not additive)
    layout.toggleLeft();
    expect(layout.store.getState().mobileFocus).toBe("left");
  });

  it("showRight/showLeft GUARANTEE visibility on mobile too, never toggling off", () => {
    layout.setTier("single");
    layout.setMobileFocus("left");

    layout.showRight();
    expect(layout.store.getState().mobileFocus).toBe("right");

    layout.showRight(); // calling it again while already showing must not flip it back off
    expect(layout.store.getState().mobileFocus).toBe("right");
  });

  it("switching tier back to full/narrow restores flag-driven toggling, mobileFocus untouched", () => {
    layout.setTier("single");
    layout.toggleRight();
    expect(layout.store.getState().mobileFocus).toBe("right");

    layout.setTier("full");
    layout.toggleRight();
    expect(layout.store.getState().rightCollapsed).toBe(true);
    expect(layout.store.getState().mobileFocus).toBe("right"); // left as-is, not reset
  });
});
