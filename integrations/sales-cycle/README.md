# sales-cycle — HubSpot-mapped meetings, feature-request capture, product-repo automation

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern —
mapping a Vexa meeting to the right customer, capturing feature requests as a distinct entity, and
turning an approved one into a pushed branch — and depends only on Vexa's own external HTTP surface
plus one narrow hook in `core/agent/control_plane/transcription_watcher.py` (`subject_resolver`)._

Application code, not platform mechanism (P11) — see `/Users/julian/.claude/plans/delightful-strolling-possum.md`
for the full design. Not intended to upstream to `Vexa-ai/vexa`.

## Public surface

- `POST /dispatch` — wraps `POST /bots`; takes an optional `customer_tag` and resolves+stores which
  customer workspace this meeting belongs to, before the bot joins.
- `POST /tag` — resolve-or-create the Redis mapping for a `meeting_id` given a `customer_tag`, without
  dispatching a bot (used by the Slack slash-command path when tagging happens after a manual dispatch).

## Depends on

- Vexa's gateway (`POST /bots`) — reached as a normal external HTTP client, X-API-Key passed through
  from the caller, never generated or stored here.
- HubSpot's REST API (plain HTTP, no SDK — Category-A licensing per this repo's ADR-0004 stance).
- Redis (`sales:meeting:{mid}:subject` — the one key this package owns; consumed by the
  `subject_resolver` hook in `transcription_watcher.py`, nothing else reads or writes it).

## Not yet built (see the plan for phased order)

Calendar-path auto-resolution, the `feature_request` card-kind config, the approval/orchestrator/push
flow, and outbound notification all land in later phases.
