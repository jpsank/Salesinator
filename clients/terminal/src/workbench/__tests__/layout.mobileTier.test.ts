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

  // Settings (and any other openTab caller invoked from a pane other than center, e.g. the LEFT
  // sidebar's gear button) needs a way to ensure the CENTER pane — where its tab actually opens —
  // becomes the visible one on mobile. Reproduced live: tapping Settings from the left pane on a
  // phone left mobileFocus at "left", so the settings tab opened underneath but nothing visible
  // changed.
  it("showCenter GUARANTEES center visibility on mobile, same never-toggle-off contract", () => {
    layout.setTier("single");
    layout.setMobileFocus("left");

    layout.showCenter();
    expect(layout.store.getState().mobileFocus).toBe("center");

    layout.showCenter(); // calling it again while already showing must not flip it back off
    expect(layout.store.getState().mobileFocus).toBe("center");
  });

  it("showCenter is a no-op outside the single-pane tier — center has no collapse flag there", () => {
    expect(layout.store.getState().tier).toBe("full");
    layout.setMobileFocus("left"); // simulate a stale/irrelevant value at a wide tier

    layout.showCenter();
    expect(layout.store.getState().mobileFocus).toBe("left"); // untouched
  });

  describe("opening content brings the center pane forward", () => {
    function attachFakeApi(svc: LayoutService) {
      const panels = new Map<string, { id: string }>();
      const api = {
        panels: [] as unknown[],
        getPanel: (id: string) => (panels.has(id) ? { id, api: { setActive() {}, setTitle() {}, updateParameters() {}, close: () => { panels.delete(id); } } } : undefined),
        addPanel: (d: { id: string }) => { panels.set(d.id, { id: d.id }); },
        onDidActivePanelChange: () => ({ dispose() {} }),
        onDidRemovePanel: () => ({ dispose() {} }),
        onDidLayoutChange: () => ({ dispose() {} }),
        toJSON: () => ({}), fromJSON: () => {}, clear: () => panels.clear(),
        activePanel: undefined, groups: [] as unknown[],
      };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      svc.attach(api as any);
    }
    const tab = { id: "meeting:1", title: "M", kind: "meeting", params: {}, context: null };

    it.each(["openTab", "openPreview", "openTabBeside"] as const)("%s switches a sidebar/chat-focused phone to the center pane", (method) => {
      attachFakeApi(layout);
      layout.setTier("single");
      layout.setMobileFocus("left");
      layout[method](tab);
      expect(layout.store.getState().mobileFocus).toBe("center");
    });

    it("leaves mobileFocus alone at wider tiers", () => {
      attachFakeApi(layout);
      layout.setMobileFocus("left");
      layout.openTab(tab);
      expect(layout.store.getState().mobileFocus).toBe("left");
    });
  });
});
