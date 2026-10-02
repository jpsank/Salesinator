/**
 * #806 — Zoom admission verdicts are TYPED (AdmissionError), not plain Errors.
 *
 * Every Zoom admission failure used to throw a bare Error, so the driver's `instanceof
 * AdmissionError` check missed it and the orchestrator blanket-mapped it to a TRANSIENT
 * `join_failure` — the retry classifier then RE-SPAWNED bots against meetings that had
 * DENIED them (a host rejection re-knocked 3×; the RTMS anti-bot wall was retried into
 * the same wall), burning user quota on permanent verdicts.
 *
 * Drives the SHIPPED waitForZoomMeetingAdmission over a fabricated Page whose `evaluate`
 * runs the probe fns in-node against a fake `document` (same no-browser pattern as
 * jitsi/admission.test.ts).
 *
 * Run: npx tsx src/zoom/admission.test.ts
 */

import { waitForZoomMeetingAdmission, checkForZoomAdmissionSilent } from "./admission";
import { zoomMeetingAppSelector } from "./selectors";
import { AdmissionError } from "../shared/admission";
import { resetEscalation } from "../shared/escalation";

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ""}`); failed++; }
}

/** A page whose DOM is just body text: evaluate() runs the probe in-node, locators see nothing. */
function makePage(bodyText: () => string): any {
  const locator = (sel: string): any => ({
    first: () => locator(sel),
    isVisible: async () => false,
    click: async () => {},
    count: async () => 0,
  });
  return {
    evaluate: async (fn: any, arg?: any) => {
      (globalThis as any).document = { body: { innerText: bodyText() } };
      try { return fn(arg); } finally { delete (globalThis as any).document; }
    },
    locator,
    waitForTimeout: async (_ms: number) => {},
  };
}

async function outcomeOf(run: Promise<unknown>): Promise<string> {
  try { await run; return "resolved"; }
  catch (e: any) { return e instanceof AdmissionError ? `AdmissionError:${e.outcome}` : `Error:${e.message}`; }
}

async function main() {
  const cfg: any = {};

  // NB on escalation: once checkEscalation fires (elapsed > 80% of timeout, or >10s of unknown
  // state), the module latches a 5-MINUTE timeout extension — with a no-op waitForTimeout that
  // turns the poll into a minutes-long busy spin. Each case below is shaped to conclude before
  // any escalation threshold: the timeout case uses timeoutMs=0 (the poll loop is never entered),
  // and the rejection flips on the second iteration (~4s of logical unknown time, under the 10s
  // bar). resetEscalation() between cases keeps the latch from leaking across them.

  console.log("\n=== admission never granted → typed TRANSIENT lobby_timeout ===");
  {
    resetEscalation();
    const page = makePage(() => "");
    const got = await outcomeOf(waitForZoomMeetingAdmission(page, 0, cfg));
    check("timeout → AdmissionError('lobby_timeout') — the legit retry case stays a retry",
      got === "AdmissionError:lobby_timeout", got);
  }

  console.log("\n=== RTMS anti-bot wall (pre-poll) → typed PERMANENT denial ===");
  {
    resetEscalation();
    const page = makePage(() => "Automated bots aren't allowed. This meeting must use Zoom RTMS.");
    const got = await outcomeOf(waitForZoomMeetingAdmission(page, 4000, cfg));
    check("anti-bot wall → AdmissionError('denial'), never a retried join_failure",
      got === "AdmissionError:denial", got);
  }

  console.log("\n=== host rejection during the poll → typed PERMANENT denial ===");
  {
    resetEscalation();
    // First evaluate calls see a neutral page (no wall, not admitted), then the removal text lands.
    let polled = 0;
    const page = makePage(() => (polled++ < 3 ? "" : "You have been removed from the meeting"));
    const got = await outcomeOf(waitForZoomMeetingAdmission(page, 30_000, cfg));
    check("host rejection → AdmissionError('denial') — a re-knock cannot succeed",
      got === "AdmissionError:denial", got);
  }

  // ── the waiting room is recognised however Zoom formats its sentence ──
  // Zoom renders "Host has joined. We've let them know you're here." with a typographic apostrophe, in separate elements
  // (so a line break lands between the sentences), and inside the same `.meeting-app` shell as a real meeting. An exact
  // substring match missed it, the shell fallback read "admitted", and the bot reported itself in the meeting while it sat
  // in the host's waiting room (seen live, 2026-10-02). The shell is visible and the Leave button is not, as there.
  function pageWithShell(bodyText: string): any {
    const page = makePage(() => bodyText);
    const plain = page.locator;
    page.locator = (sel: string): any => {
      const l = plain(sel);
      l.isVisible = async () => sel === zoomMeetingAppSelector;
      l.first = () => l;
      return l;
    };
    return page;
  }
  {
    const cases: [string, string][] = [
      ["a curly apostrophe", "Host has joined. We\u2019ve let them know you\u2019re here."],
      ["a line break between the sentences", "Host has joined.\nWe've let them know you're here."],
      ["both, with extra spacing", "  Host has joined.\n\n  We\u2019ve   let them know you\u2019re here.  "],
      ["different capitalisation", "HOST HAS JOINED. WE'VE LET THEM KNOW YOU'RE HERE."],
    ];
    for (const [what, text] of cases) {
      const admitted = await checkForZoomAdmissionSilent(pageWithShell(text));
      check(`waiting room with ${what} is not read as admitted`, admitted === false, `admitted=${admitted}`);
    }
    const inMeeting = await checkForZoomAdmissionSilent(pageWithShell("Julian Sanker  Vexa  Participants  Chat  Share Screen"));
    check("a meeting shell with no waiting-room text is still admitted", inMeeting === true, `admitted=${inMeeting}`);
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
