import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Panel, QueryView } from "./ui";
import { formatTime } from "../lib/format";

export interface UpdateStatus {
  enabled: boolean; current_version: string; development_build: boolean; latest_version: string | null;
  release_url: string; release_name: string | null; published_at: string | null; notes: string | null;
  checked_at: string | null; error: string | null; update_available: boolean;
  updater: { available: boolean };
}
interface Progress { phase: string; message: string; target_version: string | null; log_tail: string[]; updated_at?: string }

const PHASES: [string, string][] = [["queued", "Requested"], ["downloading", "Download"], ["backup", "Backup"], ["installing", "Install"], ["done", "Done"]];

export const useUpdateStatus = (enabled = true) =>
  useQuery({ queryKey: ["updates"], queryFn: () => api.get<UpdateStatus>("/admin/updates"), enabled, refetchInterval: 3_600_000 });

/** Administration → System: installed version, latest published version, release notes. */
export function UpdatesPanel() {
  const qc = useQueryClient();
  const q = useUpdateStatus();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<UpdateStatus>) => {
    setBusy(true); setErr(null);
    try { qc.setQueryData(["updates"], await fn()); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Panel title="Software updates" note="the latest release is read from GitHub once a day; nothing about this appliance is sent">
      <QueryView q={q}>
        {(u) => (
          <div style={{ display: "grid", gap: 10 }}>
            <dl className="kv">
              <dt>Installed</dt><dd><b>{u.current_version}</b>{u.development_build && <span className="muted"> (development build)</span>}</dd>
              <dt>Latest release</dt>
              <dd>
                {u.latest_version ? <a href={u.release_url} target="_blank" rel="noreferrer">{u.latest_version}</a> : "–"}
                {u.published_at && <span className="muted"> · published {formatTime(u.published_at)}</span>}{" "}
                {u.update_available ? <span className="badge warn">new version available</span>
                  : u.latest_version && !u.development_build ? <span className="badge ok">up to date</span> : null}
              </dd>
              <dt>Last check</dt>
              <dd>{u.checked_at ? formatTime(u.checked_at) : "never"}
                {u.error && <span className="badge bad" title={u.error} style={{ marginLeft: 6 }}>check failed</span>}</dd>
            </dl>
            {u.error && <div className="muted" style={{ fontSize: 12 }}>{u.error} — normal on networks without Internet access.</div>}
            {u.update_available && u.updater.available && <UpdateNow version={u.latest_version as string} />}
            {u.update_available && !u.updater.available && (
              <div className="notice ok" style={{ background: "var(--accent-soft)", color: "var(--text)" }}>
                <b>{u.release_name ?? `Version ${u.latest_version}`}</b> is available. This installation cannot update itself
                (development stack, or installed before 0.16.0): download the package from the{" "}
                <a href={u.release_url} target="_blank" rel="noreferrer">release page</a>, then on the appliance:
                <code style={{ display: "block", marginTop: 6 }}>tar xzf sflow-analytics-{u.latest_version}.tar.gz &amp;&amp; cd sflow-analytics-{u.latest_version}/ &amp;&amp; sudo ./install.sh</code>
                <span className="muted" style={{ fontSize: 12 }}>Data and settings are kept; later versions can then be installed from this page.</span>
              </div>
            )}
            <UpdateProgress />
            {u.notes && (u.update_available || u.development_build) && (
              <details>
                <summary>Release notes of {u.latest_version}</summary>
                <pre className="release-notes">{u.notes}</pre>
              </details>
            )}
            <span className="inline-edit">
              <button disabled={busy} onClick={() => run(() => api.post<UpdateStatus>("/admin/updates/check"))}>Check now</button>
              <label className="pick">
                <input type="checkbox" checked={u.enabled} disabled={busy}
                  onChange={(e) => run(() => api.put<UpdateStatus>("/admin/updates", { enabled: e.target.checked }))} />
                check automatically every day
              </label>
              {err && <span className="error" style={{ padding: 0 }}>{err}</span>}
            </span>
          </div>
        )}
      </QueryView>
    </Panel>
  );
}

function UpdateNow({ version }: { version: string }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!window.confirm(`Install version ${version} now?

A backup is made first. The web interface restarts during the update (about 2-5 minutes); sFlow collection is interrupted for a few seconds.`)) return;
    setBusy(true); setErr(null);
    try {
      await api.post("/admin/updates/apply", { version });
      qc.invalidateQueries({ queryKey: ["update-progress"] });
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="notice ok" style={{ background: "var(--accent-soft)", color: "var(--text)" }}>
      <span className="inline-edit">
        <b>Version {version} is available.</b>
        <button className="primary" disabled={busy} onClick={go}>Update now</button>
        <span className="muted" style={{ fontSize: 12 }}>backup first, data and settings kept</span>
        {err && <span className="error" style={{ padding: 0 }}>{err}</span>}
      </span>
    </div>
  );
}

/** Progress of the update: polled every 3 s; the API restarts during the installation. */
function UpdateProgress() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["update-progress"], queryFn: () => api.get<{ progress: Progress | null; running: boolean }>("/admin/updates/progress"),
    refetchInterval: (query) => (query.state.data?.running || query.state.error ? 3000 : 60_000), retry: false,
  });
  const p = q.data?.progress;
  const restarting = !!q.error;
  if (!p && !restarting) return null;
  if (p && (p.phase === "done" || p.phase === "failed") && !q.data?.running) {
    const age = p.updated_at ? Date.now() - new Date(p.updated_at).getTime() : 0;
    if (age > 24 * 3600 * 1000) return null;
  }
  const idx = PHASES.findIndex(([k]) => k === p?.phase);
  if (p?.phase === "done") qc.invalidateQueries({ queryKey: ["updates"] });
  return (
    <div className="update-progress">
      <div className="steps">
        {PHASES.map(([k, label], i) => (
          <span key={k} className={p?.phase === "failed" && i === Math.max(idx, 0) ? "bad" : i < idx || p?.phase === "done" ? "done" : i === idx ? "on" : ""}>{label}</span>
        ))}
      </div>
      <div>
        {restarting ? <b>The application is restarting… this page reconnects by itself.</b>
          : p?.phase === "done" ? <><b>{p.message}.</b> <button className="primary" onClick={() => window.location.reload()}>Reload the page</button></>
          : p?.phase === "failed" ? <span className="error" style={{ padding: 0 }}>{p.message}</span>
          : <b>{p?.message}</b>}
      </div>
      {p?.log_tail?.length ? (
        <details open={p.phase === "failed"}>
          <summary>Log</summary>
          <pre className="release-notes">{p.log_tail.join(String.fromCharCode(10))}</pre>
        </details>
      ) : null}
    </div>
  );
}
