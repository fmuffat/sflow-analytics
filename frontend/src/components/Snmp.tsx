import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Panel, QueryView } from "./ui";
import { formatTime } from "../lib/format";

interface SnmpConfig {
  enabled: boolean;
  version: "v2c" | "v3";
  port: number;
  timeout: number;
  interval_minutes: number;
  community_set: boolean;
  v3: { username: string; auth_protocol: string; priv_protocol: string; auth_key_set: boolean; priv_key_set: boolean };
  targets: string[];
  last_poll: null | {
    ok: boolean; started_at: string; duration_ms: number; skipped?: boolean; error?: string;
    switches?: number; targets?: number; ports?: number; lldp_neighbors?: number; errors?: Record<string, string>;
  };
}

/** Administration > Integrations: SNMP polling of the switches no controller knows (write-only secrets). */
export function SnmpPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["snmp"], queryFn: () => api.get<SnmpConfig>("/admin/enrichment/snmp"), refetchInterval: 30_000 });
  const [form, setForm] = useState({ enabled: false, version: "v2c", port: 161, interval: 15, community: "",
    user: "", authProto: "SHA", authKey: "", privProto: "AES", privKey: "", testIp: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (q.data) {
      const d = q.data;
      setForm((f) => ({ ...f, enabled: d.enabled, version: d.version, port: d.port, interval: d.interval_minutes,
        user: d.v3.username, authProto: d.v3.auth_protocol, privProto: d.v3.priv_protocol }));
    }
  }, [q.data]);

  const fail = (e: unknown) => setMsg({ ok: false, text: (e as Error).message });
  const save = useMutation({
    mutationFn: () => api.put<SnmpConfig>("/admin/enrichment/snmp", {
      enabled: form.enabled, version: form.version, port: form.port, interval_minutes: form.interval,
      community: form.community || undefined,
      v3: { username: form.user, auth_protocol: form.authProto, auth_key: form.authKey || undefined,
        priv_protocol: form.privProto, priv_key: form.privKey || undefined },
    }),
    onSuccess: () => { setForm((f) => ({ ...f, community: "", authKey: "", privKey: "" })); setMsg({ ok: true, text: "Settings saved." }); qc.invalidateQueries({ queryKey: ["snmp"] }); },
    onError: fail,
  });
  const test = useMutation({
    mutationFn: () => api.post<{ ip: string; name: string; model: string; firmware: string; interfaces: number; duration_ms: number }>(
      "/admin/enrichment/snmp/test", { ip: form.testIp || undefined }),
    onSuccess: (r) => setMsg({ ok: true, text: `${r.ip} answers: ${r.name || "(no sysName)"}${r.model ? ` · ${r.model}` : ""}${r.firmware ? ` ${r.firmware}` : ""} · ${r.interfaces} interfaces (${r.duration_ms} ms).` }),
    onError: fail,
  });
  const poll = useMutation({
    mutationFn: () => api.post<SnmpConfig["last_poll"]>("/admin/enrichment/snmp/poll"),
    onSuccess: (r) => { setMsg(r?.ok ? { ok: true, text: `Polled ${r.switches}/${r.targets} switch(es).` } : { ok: false, text: r?.error ?? "no switch answered" }); qc.invalidateQueries(); },
    onError: fail,
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: k === "port" || k === "interval" ? Number(e.target.value)
      : k === "enabled" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <Panel title="SNMP" note="names, port descriptions and LLDP neighbors of the switches no controller knows · read-only">
      <QueryView q={q}>
        {(d) => (
          <div style={{ display: "grid", gap: 10, maxWidth: 560 }}>
            <form className="filters" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
              <label style={{ flexDirection: "row", alignItems: "center", gap: 6, gridColumn: "1 / -1" }}>
                <input type="checkbox" checked={form.enabled} onChange={set("enabled")} /> Poll the switches with SNMP
              </label>
              <label>Version
                <select value={form.version} onChange={set("version")}>
                  <option value="v2c">SNMP v2c (community)</option>
                  <option value="v3">SNMPv3 (user)</option>
                </select>
              </label>
              <label>Poll every (minutes)<input type="number" min={5} max={1440} value={form.interval} onChange={set("interval")} /></label>
              {form.version === "v2c" ? (
                <label>Community
                  <input type="password" value={form.community} onChange={set("community")} autoComplete="new-password"
                    placeholder={d.community_set ? "stored (leave empty to keep)" : "read-only community"} />
                </label>
              ) : (
                <>
                  <label>User<input value={form.user} onChange={set("user")} autoComplete="off" /></label>
                  <label>Authentication
                    <select value={form.authProto} onChange={set("authProto")}>{["SHA", "SHA-256", "SHA-512", "MD5"].map((p) => <option key={p}>{p}</option>)}</select>
                  </label>
                  <label>Authentication key
                    <input type="password" value={form.authKey} onChange={set("authKey")} autoComplete="new-password"
                      placeholder={d.v3.auth_key_set ? "stored (leave empty to keep)" : "8 characters or more"} />
                  </label>
                  <label>Privacy
                    <select value={form.privProto} onChange={set("privProto")}>
                      {[["AES", "AES-128"], ["AES-256", "AES-256"], ["DES", "DES"], ["none", "none (authNoPriv)"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </label>
                  {form.privProto !== "none" && (
                    <label>Privacy key
                      <input type="password" value={form.privKey} onChange={set("privKey")} autoComplete="new-password"
                        placeholder={d.v3.priv_key_set ? "stored (leave empty to keep)" : "8 characters or more"} />
                    </label>
                  )}
                </>
              )}
              <label>UDP port<input type="number" min={1} max={65535} value={form.port} onChange={set("port")} /></label>
              <label>Test on IP <span className="muted">(optional)</span><input value={form.testIp} onChange={set("testIp")} placeholder={d.targets[0] ?? "switch IP"} /></label>
              <span className="inline-edit" style={{ gridColumn: "1 / -1" }}>
                <button className="primary" disabled={save.isPending}>Save</button>
                <button type="button" disabled={test.isPending} onClick={() => test.mutate()}>{test.isPending ? "Testing…" : "Test"}</button>
                <button type="button" disabled={!d.enabled || poll.isPending} onClick={() => poll.mutate()}>{poll.isPending ? "Polling…" : "Poll now"}</button>
              </span>
            </form>
            {msg && <div className={msg.ok ? "muted" : "error"} style={{ padding: 0 }}>{msg.text}</div>}
            <div className="muted" style={{ fontSize: 12 }}>
              Polled: the switches that send sFlow and that RUCKUS One / SmartZone does not know
              ({d.targets.length ? d.targets.join(", ") : "none"}). On the ICX: <code>snmp-server community &lt;name&gt; ro</code>, or an
              SNMPv3 user (<code>snmp-server user …</code>). Standard MIBs only: sysName, ifName, ifAlias, LLDP. Secrets are
              encrypted at rest and never displayed again.
            </div>
            {d.last_poll && !d.last_poll.skipped && (
              <dl className="kv">
                <dt>Last poll</dt>
                <dd>
                  <span className={"badge " + (d.last_poll.ok ? "ok" : "bad")}>{d.last_poll.ok ? "ok" : "failed"}</span>{" "}
                  {formatTime(d.last_poll.started_at, true)} · {d.last_poll.duration_ms} ms
                </dd>
                {d.last_poll.error ? (
                  <><dt>Error</dt><dd className="error" style={{ padding: 0 }}>{d.last_poll.error}</dd></>
                ) : (
                  <><dt>Read</dt><dd>{d.last_poll.switches}/{d.last_poll.targets} switches · {d.last_poll.ports} interfaces · {d.last_poll.lldp_neighbors} LLDP neighbors</dd></>
                )}
                {d.last_poll.errors && Object.keys(d.last_poll.errors).length > 0 && (
                  <><dt>No answer</dt><dd className="warn-text">{Object.entries(d.last_poll.errors).map(([ip, e]) => `${ip} (${e})`).join(" · ")}</dd></>
                )}
              </dl>
            )}
          </div>
        )}
      </QueryView>
    </Panel>
  );
}
