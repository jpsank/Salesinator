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
