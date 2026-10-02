import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Panel, QueryView } from "./ui";
import { formatTime } from "../lib/format";

interface EnrichmentConfig {
  source: "none" | "ruckusone" | "smartzone";
  interval_minutes: number;
  ruckusone: { region: string; tenant_id: string; client_id: string; client_secret_set: boolean };
  smartzone: { host: string; port: number; username: string; verify_tls: boolean; password_set: boolean };
  regions: Record<string, string>;
  smartzone_available: boolean;
  last_sync: null | {
    ok: boolean; started_at: string; duration_ms: number; error?: string; skipped?: boolean;
    switches?: number; ports?: number; clients?: number; lldp_neighbors?: number; clients_error?: string;
  };
}

/** Administration > Enrichment: controller source, credentials (write-only secret), test and sync. */
export function EnrichmentPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["enrichment"], queryFn: () => api.get<EnrichmentConfig>("/admin/enrichment"), refetchInterval: 30_000 });
  const [form, setForm] = useState({ source: "none", interval: 15, region: "eu", tenant: "", clientId: "", secret: "",
    szHost: "", szPort: 8443, szUser: "", szPassword: "", szVerify: true });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (q.data) {
      const d = q.data;
      setForm((f) => ({ ...f, source: d.source, interval: d.interval_minutes, region: d.ruckusone.region,
        tenant: d.ruckusone.tenant_id, clientId: d.ruckusone.client_id,
        szHost: d.smartzone.host, szPort: d.smartzone.port, szUser: d.smartzone.username, szVerify: d.smartzone.verify_tls }));
    }
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => api.put<EnrichmentConfig>("/admin/enrichment", {
      source: form.source, interval_minutes: form.interval,
      ruckusone: { region: form.region, tenant_id: form.tenant, client_id: form.clientId, client_secret: form.secret || undefined },
      smartzone: { host: form.szHost, port: form.szPort, username: form.szUser, password: form.szPassword || undefined, verify_tls: form.szVerify },
    }),
    onSuccess: () => { setForm((f) => ({ ...f, secret: "", szPassword: "" })); setMsg({ ok: true, text: "Settings saved." }); qc.invalidateQueries({ queryKey: ["enrichment"] }); },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  });
  const test = useMutation({
    mutationFn: () => api.post<{ switches: number; duration_ms: number; api_version?: string }>("/admin/enrichment/test"),
    onSuccess: (r) => setMsg({ ok: true, text: `Connection OK: ${r.switches} switch(es) visible (${r.duration_ms} ms${r.api_version ? `, API ${r.api_version}` : ""}).` }),
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  });
  const sync = useMutation({
    mutationFn: () => api.post<EnrichmentConfig["last_sync"]>("/admin/enrichment/sync"),
    onSuccess: (r) => { setMsg(r?.ok ? { ok: true, text: "Synchronization done." } : { ok: false, text: r?.error ?? "failed" }); qc.invalidateQueries(); },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: k === "interval" || k === "szPort" ? Number(e.target.value)
      : k === "szVerify" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <Panel title="Enrichment" note="switch names, ports and LLDP neighbors from a controller · read-only · sFlow works without it">
      <QueryView q={q}>
        {(d) => (
          <div style={{ display: "grid", gap: 10, maxWidth: 560 }}>
            <form className="filters" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
              <label>Source
                <select value={form.source} onChange={set("source")}>
                  <option value="none">None</option>
                  <option value="ruckusone">RUCKUS One</option>
                  <option value="smartzone" disabled={!d.smartzone_available}>SmartZone / vSZ{d.smartzone_available ? "" : " (coming soon)"}</option>
                </select>
              </label>
              <label>Sync every (minutes)<input type="number" min={5} max={1440} value={form.interval} onChange={set("interval")} /></label>
              {form.source === "ruckusone" && (
                <>
                  <label>Region
                    <select value={form.region} onChange={set("region")}>
                      {Object.entries(d.regions).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                  </label>
                  <label>Tenant ID<input value={form.tenant} onChange={set("tenant")} placeholder="from the RUCKUS One URL" /></label>
                  <label>Client ID<input value={form.clientId} onChange={set("clientId")} autoComplete="off" /></label>
                  <label>Client secret
                    <input type="password" value={form.secret} onChange={set("secret")} autoComplete="new-password"
                      placeholder={d.ruckusone.client_secret_set ? "stored (leave empty to keep)" : "application token secret"} />
                  </label>
                </>
              )}
              {form.source === "smartzone" && (
                <>
                  <label>SmartZone host<input value={form.szHost} onChange={set("szHost")} placeholder="vsz.example.net" /></label>
                  <label>API port<input type="number" min={1} max={65535} value={form.szPort} onChange={set("szPort")} /></label>
                  <label>Username (read-only admin)<input value={form.szUser} onChange={set("szUser")} autoComplete="off" /></label>
                  <label>Password
                    <input type="password" value={form.szPassword} onChange={set("szPassword")} autoComplete="new-password"
                      placeholder={d.smartzone.password_set ? "stored (leave empty to keep)" : "password"} />
                  </label>
                  <label style={{ flexDirection: "row", alignItems: "center", gap: 6, gridColumn: "1 / -1" }}>
                    <input type="checkbox" checked={form.szVerify} onChange={set("szVerify")} />
                    Verify the SmartZone TLS certificate (untick for a self-signed certificate)
                  </label>
                </>
              )}
              <span className="inline-edit" style={{ gridColumn: "1 / -1" }}>
                <button className="primary" disabled={save.isPending}>Save</button>
                <button type="button" disabled={d.source === "none" || test.isPending} onClick={() => test.mutate()}>{test.isPending ? "Testing…" : "Test connection"}</button>
                <button type="button" disabled={d.source === "none" || sync.isPending} onClick={() => sync.mutate()}>{sync.isPending ? "Syncing…" : "Sync now"}</button>
              </span>
            </form>
            {msg && <div className={msg.ok ? "muted" : "error"} style={{ padding: 0 }}>{msg.text}</div>}
            <div className="muted" style={{ fontSize: 12 }}>
              RUCKUS One: create an application token (read-only is enough) in Administration → API. SmartZone: create a
              read-only administrator. Secrets are encrypted at rest and never displayed again.
            </div>
            {d.last_sync && !d.last_sync.skipped && (
              <dl className="kv">
                <dt>Last sync</dt>
                <dd>
                  <span className={"badge " + (d.last_sync.ok ? "ok" : "bad")}>{d.last_sync.ok ? "ok" : "failed"}</span>{" "}
                  {formatTime(d.last_sync.started_at, true)} · {d.last_sync.duration_ms} ms
                </dd>
                {d.last_sync.ok ? (
                  <><dt>Imported</dt><dd>{d.last_sync.switches} switches · {d.last_sync.ports} ports · {d.last_sync.lldp_neighbors} LLDP neighbors · {d.last_sync.clients} clients</dd></>
                ) : (
                  <><dt>Error</dt><dd className="error" style={{ padding: 0 }}>{d.last_sync.error}</dd></>
                )}
              </dl>
            )}
          </div>
        )}
      </QueryView>
    </Panel>
  );
}
