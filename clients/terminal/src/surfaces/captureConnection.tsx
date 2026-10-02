"use client";
/** Settings → Integrations → Vexa Capture (Mac). The Mac app notices a Zoom/Teams call and sends Vexa's bot to it (or captures
 *  the audio); pairing starts HERE, where the person is already signed in: Connect asks the server for a one-time code and opens
 *  the app with it — no address to type. Each paired Mac is one bot-scoped key, listed with when it last did anything, and
 *  Disconnect revokes it. */
import { useCallback, useEffect, useState } from "react";
import { cardBtn, cardMeta, cardPrimaryBtn, cardRow } from "./integrationCard";
import { jsonOrThrow } from "./salesCycleApi";
import { presentError } from "./apiClient";

export interface CaptureDevice { id: number; created_at: string | null; last_used_at: string | null }

export const getCaptureDevices = async (): Promise<CaptureDevice[]> =>
  (await jsonOrThrow<{ devices: CaptureDevice[] }>(await fetch("/api/capture/status", { cache: "no-store" }))).devices;

export const startPairing = async (): Promise<string> =>
  (await jsonOrThrow<{ link: string }>(await fetch("/api/capture/pair", { method: "POST" }))).link;

export const disconnectDevice = async (id: number): Promise<void> => {
  await jsonOrThrow(await fetch("/api/capture/revoke", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) }));
};

/** "just now", "5 min ago", "3 h ago", "2 days ago" — or "not used yet". */
export function lastUsed(iso: string | null, now = Date.now()): string {
  if (!iso) return "not used yet";
  const t = Date.parse(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return "not used yet";
  const min = Math.max(0, Math.round((now - t) / 60000));
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} days ago`;
}

export function CaptureCard({ now = () => Date.now() }: { now?: () => number }) {
  const [devices, setDevices] = useState<CaptureDevice[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState(false);

  const refresh = useCallback(() => {
    getCaptureDevices().then((d) => { setDevices(d); setErr(null); }).catch((e: unknown) => setErr(presentError(e).headline));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const connect = async () => {
    setBusy(true); setErr(null);
    try {
      const link = await startPairing();
      setOpened(true);
      window.location.href = link;                                   // the browser asks to open the app; this page stays put
      setTimeout(refresh, 6000);                                     // pick up the new Mac once the app has paired
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const remove = async (id: number) => {
    setBusy(true); setErr(null);
    try { await disconnectDevice(id); refresh(); }
    catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const n = devices?.length ?? 0;
  return (
    <div style={{ maxWidth: 640 }}>
      <div style={cardRow}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--t1)" }}>Vexa Capture (Mac)</span>
          <span style={{ flex: 1, fontSize: 11.5, color: "var(--t3)" }}>
            {devices === null ? "Checking…" : n === 0 ? "No Mac connected" : n === 1 ? "1 Mac connected" : `${n} Macs connected`}
          </span>
          <button disabled={busy} onClick={() => void connect()} style={cardPrimaryBtn}>{n === 0 ? "Connect a Mac" : "Connect another Mac"}</button>
        </div>
        <div style={cardMeta}>
          A menu-bar app that notices when you join a Zoom or Teams call that isn&rsquo;t on a calendar and sends Vexa&rsquo;s bot to it
          (or, with no link to find, captures the call&rsquo;s audio on your Mac).
        </div>
        {(devices ?? []).map((d, i) => (
          <div key={d.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color: "var(--t2)" }}>
            <span style={{ flex: 1 }}>Mac {i + 1} · last used {lastUsed(d.last_used_at, now())}</span>
            <button disabled={busy} onClick={() => void remove(d.id)} style={{ ...cardBtn, color: "var(--danger)" }}>Disconnect</button>
          </div>
        ))}
        {opened && (
          <div role="status" style={cardMeta}>
            Allow your browser to open Vexa Capture. Nothing happened? Install the app first (<code style={{ fontFamily: "var(--mono)" }}>clients/capture-mac/install.sh</code>), then choose Connect again.
          </div>
        )}
        {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</div>}
      </div>
    </div>
  );
}
