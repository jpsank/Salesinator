// What a preview may ask of the real stack. A preview shows real data and can change none of it, so
// every request that is not a plain read is refused here, whatever the preview's code tries to send.

const READ_METHODS = new Set(["GET", "HEAD"]);

/** The gateway: reads, and the live-updates socket (an upgrade is a GET on /ws). */
export function allowGateway(method, path) {
  return READ_METHODS.has(method) && !path.split("?")[0].split("/").includes("..");
}

/** The admin API is never reachable from a preview; the gate answers the one question a preview
 *  may ask of it (who is this session?) itself. */
export function isWhoAmI(method, path) {
  return method === "POST" && path.split("?")[0] === "/internal/validate";
}

export const READ_ONLY_REFUSAL = {
  detail: "This is a preview of a proposed change, so it can only show data — it cannot change anything.",
};
