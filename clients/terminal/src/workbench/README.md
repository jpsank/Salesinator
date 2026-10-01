# workbench

The shell. The parts layout (activity bar · primary sidebar · main · auxiliary · panel · status bar)
and the `LayoutService` that toggles/sizes them, plus the `Composer` that turns `/`-input into commands
and routes plain text to the active surface's `onSubmit`. Surfaces are contributed (see `../surfaces/`),
never hardcoded here — adding a surface is a `registerSurface` call, not an edit to the shell (P2/P6 on
the client). VSCode-inspired: parts + a contribution registry + DI services (`../platform`).

## Responsive tiers

`Workbench.tsx` derives a tier from the window width (never from, or into, the user's saved sizes) and
pushes it to `LayoutService`: `full` (900px and up), `narrow` (560–899px), and `single` (under 560px).
In `single` exactly one of the left, center and right panes shows, chosen by `mobileFocus`; opening a
tab, a tab beside another, or a preview brings the center pane forward. Settings follows the same
tier: its section list becomes a horizontal strip and its forms wrap.
