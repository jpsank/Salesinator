# api/github/oauth/__tests__

[`authorize.test.ts`](authorize.test.ts) — asserts `authorize/route.ts` builds the correct full
gateway URL, without double-prepending the `/api/` segment the gateway's own `/agent/` prefix
already adds.
