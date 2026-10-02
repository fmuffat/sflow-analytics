import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useCanEdit } from "../hooks/role";
import type { Interface } from "../api/types";
import { useExporters, useInterfaces } from "../hooks/queries";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView } from "../components/ui";
import { formatTime } from "../lib/format";

export interface Group { kind: "ip" | "if"; name: string; members: string[]; notes: string; updated_at: string }

export const useGroups = (kind?: "ip" | "if") =>
  useQuery({ queryKey: ["groups", kind ?? ""], queryFn: () => api.get<{ items: Group[] }>("/groups", undefined, kind ? { kind } : {}) });

const EMPTY = { name: "", members: [] as string[], text: "", notes: "" };

/** IP groups ("Servers", "Guest Wi-Fi") and interface groups ("Uplinks"). */
export function Groups() {
  const qc = useQueryClient();
  const all = useGroups();
  const [kind, setKind] = useState<"ip" | "if">("ip");
  const [form, setForm] = useState(EMPTY);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const save = useMutation({
    mutationFn: () => api.put("/groups", {
      kind, name: form.name.trim(), notes: form.notes,
      members: kind === "ip" ? form.text.split(/[\s,;]+/).filter(Boolean) : form.members,
    }),
    onSuccess: () => { setMsg({ ok: true, text: "Group saved." }); setForm(EMPTY); qc.invalidateQueries(); },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  });
  const remove = async (g: Group) => {
    await api.del(`/groups?${new URLSearchParams({ kind: g.kind, name: g.name })}`);
    qc.invalidateQueries();
  };
  const editGroup = (g: Group) => {
    setKind(g.kind);
    setForm({ name: g.name, members: g.members, text: g.members.join("\n"), notes: g.notes });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const canEdit = useCanEdit();
  return (
    <>
      <div className="page-head">
        <h1>Groups</h1>
        <span className="sub">use them as filters, in "Groups" views and in the flow map</span>
      </div>
      {canEdit && <Panel title={form.name ? `Edit group` : "New group"}>
        <form style={{ display: "grid", gap: 10, maxWidth: 760 }} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <span className="inline-edit">
            <span className="seg">
              <button type="button" className={kind === "ip" ? "on" : ""} onClick={() => setKind("ip")}>IP group</button>
              <button type="button" className={kind === "if" ? "on" : ""} onClick={() => setKind("if")}>Interface group</button>
            </span>
            <input placeholder="Name, e.g. Servers" maxLength={48} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input placeholder="Notes" maxLength={500} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} style={{ flex: 1 }} />
          </span>
          {kind === "ip" ? (
            <label>IP addresses and subnets (one per line or comma-separated)
              <textarea rows={5} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })}
                placeholder={"192.168.1.30\n10.0.0.0/24\n2001:db8::/48"} className="mono" />
            </label>
          ) : (
            <InterfacePicker selected={form.members} onChange={(members) => setForm({ ...form, members })} />
          )}
          <span className="inline-edit">
            <button className="primary" disabled={!form.name.trim() || save.isPending}>Save group</button>
            {form.name && <button type="button" onClick={() => setForm(EMPTY)}>New</button>}
            {msg && <span className={msg.ok ? "muted" : "error"} style={{ padding: 0 }}>{msg.text}</span>}
          </span>
        </form>
      </Panel>}
      <div style={{ marginTop: 12 }}>
        <Panel title="Groups" flush>
          <QueryView q={all}>
            {(d) => (
              <DataTable<Group>
                rows={d.items}
                rowKey={(g) => g.kind + g.name}
                empty="No group yet."
                initialSort={{ key: "name", desc: false }}
                columns={[
                  { key: "name", label: "Name", render: (g) => <b>{g.name}</b>, sort: (g) => g.name.toLowerCase() },
                  { key: "kind", label: "Type", render: (g) => (g.kind === "ip" ? "IP" : "Interfaces"), sort: (g) => g.kind },
                  { key: "members", label: "Members", render: (g) => <span className="mono truncate" title={g.members.join(", ")}>{g.members.length} · {g.members.slice(0, 3).join(", ")}{g.members.length > 3 ? "…" : ""}</span> },
                  { key: "notes", label: "Notes", render: (g) => g.notes },
                  { key: "updated", label: "Updated", render: (g) => formatTime(g.updated_at), sort: (g) => g.updated_at },
                  { key: "act", label: "", render: (g) => !canEdit ? null : <span className="inline-edit"><button onClick={() => editGroup(g)}>Edit</button><button onClick={() => remove(g)}>Delete</button></span> },
                ]}
              />
            )}
          </QueryView>
        </Panel>
      </div>
    </>
  );
}

function InterfacePicker(props: { selected: string[]; onChange: (m: string[]) => void }) {
  const ifs = useInterfaces();
  const exporters = useExporters();
  const names = new Map(exporters.data?.items.map((e) => [e.id, e.name]) ?? []);
  const sel = new Set(props.selected);
  const toggle = (id: string) => {
    const next = new Set(sel);
    if (next.has(id)) next.delete(id); else next.add(id);
    props.onChange([...next]);
  };
  return (
    <div className="picker">
      <QueryView q={ifs}>
        {(d) => d.items.length === 0 ? <div className="empty">No interface seen yet.</div> : (
          <>{d.items.map((i: Interface) => (
            <label key={i.id} className="pick">
              <input type="checkbox" checked={sel.has(i.id)} onChange={() => toggle(i.id)} />
              {names.get(i.exporter_id) ?? i.exporter_id} · {i.label}
              {i.controller_port?.lldp_neighbor && <span className="muted"> → {i.controller_port.lldp_neighbor}</span>}
            </label>
          ))}</>
        )}
      </QueryView>
    </div>
  );
}
