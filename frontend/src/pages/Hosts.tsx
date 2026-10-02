import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useCanEdit } from "../hooks/role";
import { DataTable } from "../components/DataTable";
import { AliasDialog } from "../components/Alias";
import { Panel, QueryView } from "../components/ui";
import { formatCount, formatTime } from "../lib/format";

interface Alias { kind: "ip" | "mac" | "cidr"; key: string; name: string; notes: string; updated_at: string }
interface DnsConfig {
  enabled: boolean; servers: string[]; max_per_run: number;
  cache: { cached: number; names: number; no_name: number; errors: number };
}

const KIND_LABEL = { ip: "IP address", mac: "MAC address", cidr: "Subnet" };

export function Hosts() {
  const qc = useQueryClient();
  const aliases = useQuery({ queryKey: ["aliases"], queryFn: () => api.get<{ items: Alias[] }>("/aliases") });
  const [form, setForm] = useState<{ kind: Alias["kind"]; key: string; name: string; notes: string }>({ kind: "ip", key: "", name: "", notes: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [edit, setEdit] = useState<Alias | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const add = useMutation({
    mutationFn: () => api.put("/aliases", form),
    onSuccess: () => { setForm({ ...form, key: "", name: "", notes: "" }); setMsg({ ok: true, text: "Alias saved." }); qc.invalidateQueries(); },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  });
  const importCsv = async (file: File) => {
    try {
      const r = await api.postText<{ imported: number; error_count: number; errors: { line: number; error: string }[] }>("/aliases/import", await file.text());
      setMsg({ ok: r.error_count === 0, text: `${r.imported} alias(es) imported` + (r.error_count ? `, ${r.error_count} line(s) rejected: ${r.errors.slice(0, 3).map((e) => `line ${e.line}: ${e.error}`).join("; ")}` : ".") });
      qc.invalidateQueries();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
  };

  const canEdit = useCanEdit();
  return (
    <>
      <div className="page-head">
        <h1>Hosts &amp; aliases</h1>
        <span className="sub">name priority: IP alias › MAC alias › controller › reverse DNS › subnet alias</span>
      </div>

      {canEdit && <div style={{ marginBottom: 12 }}>
        <Panel title="Add an alias">
          <form className="filters" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }} onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <label>Type
              <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Alias["kind"] })}>
                {(Object.keys(KIND_LABEL) as Alias["kind"][]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </select>
            </label>
            <label>{KIND_LABEL[form.kind]}
              <input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })}
                placeholder={form.kind === "ip" ? "192.168.1.30" : form.kind === "mac" ? "aa:bb:cc:dd:ee:ff" : "192.168.101.0/24"} />
            </label>
            <label>Name<input maxLength={64} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="NAS-Synology" /></label>
            <label>Notes<input maxLength={500} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
            <span className="inline-edit" style={{ gridColumn: "1 / -1" }}>
              <button className="primary" disabled={!form.key.trim() || !form.name.trim() || add.isPending}>Save alias</button>
              <span className="spacer" />
              <input ref={fileRef} type="file" accept=".csv,text/csv" style={{ display: "none" }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) importCsv(f); e.target.value = ""; }} />
              <button type="button" onClick={() => fileRef.current?.click()} title="CSV: kind,key,name[,notes]">Import CSV</button>
              <a className="btn" href="/api/v1/aliases/export" download>Export CSV</a>
            </span>
          </form>
          {msg && <div className={msg.ok ? "muted" : "error"} style={{ padding: "6px 0 0" }}>{msg.text}</div>}
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            CSV format: <code>kind,key,name,notes</code> with kind = ip, mac or cidr. Tip: click ✎ next to any IP in the tables to name it.
          </div>
        </Panel>
      </div>}

      <Panel title="Aliases" flush>
        <QueryView q={aliases}>
          {(d) => (
            <DataTable<Alias>
              rows={d.items}
              rowKey={(r) => r.kind + r.key}
              empty="No alias yet."
              initialSort={{ key: "name", desc: false }}
              columns={[
                { key: "name", label: "Name", render: (r) => <b>{r.name}</b>, sort: (r) => r.name.toLowerCase() },
                { key: "kind", label: "Type", render: (r) => KIND_LABEL[r.kind], sort: (r) => r.kind },
                { key: "key", label: "Address", render: (r) => <span className="mono">{r.key}</span>, sort: (r) => r.key },
                { key: "notes", label: "Notes", render: (r) => r.notes },
                { key: "updated", label: "Updated", render: (r) => formatTime(r.updated_at), sort: (r) => r.updated_at },
                { key: "edit", label: "", render: (r) => canEdit ? <button onClick={() => setEdit(r)}>Edit</button> : null },
              ]}
            />
          )}
        </QueryView>
      </Panel>
      {edit && <AliasDialog kind={edit.kind} keyValue={edit.key} current={edit.name} onClose={() => setEdit(null)} />}
    </>
  );
}

/** Reverse DNS settings (Administration → Integrations). */
export function DnsPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dns"], queryFn: () => api.get<DnsConfig>("/admin/dns"), refetchInterval: 30_000 });
  const [form, setForm] = useState({ enabled: true, servers: "", max: 300 });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    if (q.data) setForm({ enabled: q.data.enabled, servers: q.data.servers.join(", "), max: q.data.max_per_run });
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.put("/admin/dns", { enabled: form.enabled, max_per_run: form.max,
      servers: form.servers.split(/[,\s]+/).filter(Boolean) }),
    onSuccess: () => { setMsg({ ok: true, text: "DNS settings saved." }); qc.invalidateQueries({ queryKey: ["dns"] }); },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  });
  const run = useMutation({
    mutationFn: () => api.post<{ enabled: boolean; resolved?: number; names?: number }>("/admin/dns/run"),
    onSuccess: (r) => { setMsg({ ok: true, text: r.enabled ? `${r.resolved} IP(s) looked up, ${r.names} name(s) found.` : "Reverse DNS is disabled." }); qc.invalidateQueries(); },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  });
  return (
    <Panel title="Reverse DNS" note="resolved in the background by the worker, cached 24 h">
      <QueryView q={q}>
        {(d) => (
          <div style={{ display: "grid", gap: 8 }}>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
              Resolve IP addresses to host names (PTR records)
            </label>
            <div className="filters" style={{ gridTemplateColumns: "2fr 1fr" }}>
              <label>DNS servers (empty = system resolver)
                <input value={form.servers} onChange={(e) => setForm({ ...form, servers: e.target.value })} placeholder="192.168.1.1, 10.0.0.53" />
              </label>
              <label>Lookups per minute<input type="number" min={10} max={5000} value={form.max} onChange={(e) => setForm({ ...form, max: Number(e.target.value) })} /></label>
            </div>
            <span className="inline-edit">
              <button className="primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
              <button onClick={() => run.mutate()} disabled={run.isPending || !d.enabled}>{run.isPending ? "Resolving…" : "Resolve now"}</button>
            </span>
            {msg && <div className={msg.ok ? "muted" : "error"} style={{ padding: 0 }}>{msg.text}</div>}
            <dl className="kv">
              <dt>Cache</dt>
              <dd>{formatCount(d.cache.names)} names · {formatCount(d.cache.no_name)} without PTR · {formatCount(d.cache.errors)} errors (retried after 1 h)</dd>
            </dl>
            <div className="muted" style={{ fontSize: 12 }}>
              Private addresses usually need your internal DNS server. Lookups send the IP addresses to the configured resolvers.
            </div>
          </div>
        )}
      </QueryView>
    </Panel>
  );
}
