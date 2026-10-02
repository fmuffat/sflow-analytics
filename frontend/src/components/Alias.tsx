import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useCanEdit } from "../hooks/role";

/** Small ✎ button next to an IP: sets a manual alias (highest-priority name). */
export function AliasButton(props: { ip: string; name?: string | null }) {
  const [open, setOpen] = useState(false);
  const canEdit = useCanEdit();
  if (!canEdit) return null;
  return (
    <>
      <button
        className="alias-btn" title={`Name ${props.ip}`}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(true); }}
      >✎</button>
      {open && <AliasDialog kind="ip" keyValue={props.ip} current={props.name ?? ""} onClose={() => setOpen(false)} />}
    </>
  );
}

export function AliasDialog(props: { kind: "ip" | "mac" | "cidr"; keyValue: string; current: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(props.current);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (remove = false) => {
    setBusy(true);
    setErr(null);
    try {
      if (remove) {
        await api.del(`/aliases?${new URLSearchParams({ kind: props.kind, key: props.keyValue })}`);
      } else {
        await api.put("/aliases", { kind: props.kind, key: props.keyValue, name: name.trim() });
      }
      await qc.invalidateQueries();
      props.onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-back" onClick={(e) => { e.preventDefault(); e.stopPropagation(); props.onClose(); }}>
      <form className="panel modal" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="login-brand" style={{ fontSize: 15 }}>Name {props.kind === "ip" ? "host" : props.kind} <span className="mono muted">{props.keyValue}</span></div>
        <label>Alias (overrides controller and DNS names)
          <input autoFocus maxLength={64} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. NAS-Synology" />
        </label>
        {err && <div className="error" style={{ padding: 0 }}>{err}</div>}
        <span className="inline-edit">
          <button className="primary" disabled={busy || !name.trim()}>Save</button>
          <button type="button" onClick={props.onClose}>Cancel</button>
          <span className="spacer" />
          <button type="button" disabled={busy} onClick={() => save(true)} title="Remove the manual alias">Remove alias</button>
        </span>
      </form>
    </div>
  );
}
