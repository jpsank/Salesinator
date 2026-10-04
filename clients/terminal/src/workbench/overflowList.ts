/** Dockview builds the "more tabs" list once, when it opens, and never refreshes it — a tab closed from inside it would stay listed
 *  until the list is reopened. Takes the closed tab's row out of that list, and closes the list when it was the last row. */
export function dropFromOverflowList(inside: Element | null, win: Window = window): void {
  const row = inside?.closest(".dv-tab");
  const list = row?.closest(".dv-tabs-overflow-container");
  if (!row || !list) return;
  row.remove();
  // dockview closes its popover on Escape
  if (!list.querySelector(".dv-tab")) win.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
}

/** The "more tabs" button's number is worked out by dockview only when the tab strip scrolls or resizes, so after a tab closes it keeps
 *  counting the old set (and the list it opens shows it). A scroll event on each strip makes dockview count again. Waits two frames
 *  so the closed tab is gone and the strip has re-laid out. */
export function recountOverflow(root: ParentNode = document, raf: (cb: () => void) => void = (cb) => requestAnimationFrame(cb)): void {
  raf(() => raf(() => {
    for (const strip of root.querySelectorAll(".dv-tabs-container")) strip.dispatchEvent(new Event("scroll"));
  }));
}
