import { useCanEdit } from "../hooks/role";
import type { ReactNode } from "react";
import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

export function Panel(props: { title?: ReactNode; note?: ReactNode; actions?: ReactNode; flush?: boolean; children: ReactNode }) {
  return (
    <section className="panel">
      {props.title && (
        <header>
          {props.title}
          {props.note && <span className="note">{props.note}</span>}
          <span className="spacer" />
          {props.actions}
        </header>
      )}
      <div className={props.flush ? "body flush" : "body"}>{props.children}</div>
    </section>
  );
}

export function Stat(props: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="panel stat">
      <div className="label">{props.label}</div>
      <div className="value">{props.value}</div>
      {props.hint && <div className="hint">{props.hint}</div>}
    </div>
  );
}

/** Renders loading / error states of a query, then its data. */
export function QueryView<T>(props: { q: UseQueryResult<T>; children: (data: T) => ReactNode }) {
  const { q } = props;
  if (q.isError) return <div className="error">Error: {(q.error as Error).message}</div>;
  if (q.data === undefined) return <div className="empty">Loading…</div>;
  return <>{props.children(q.data)}</>;
}

export function StatusBadge({ status }: { status: string | undefined }) {
  const cls = status === "active" || status === "running" ? "ok" : status === "inactive" ? "warn" : "bad";
  return <span className={"badge " + cls}>{status ?? "unknown"}</span>;
}

export function Tabs<T extends string>(props: { tabs: [T, string][]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs">
      {props.tabs.map(([k, label]) => (
        <button key={k} className={props.value === k ? "on" : ""} onClick={() => props.onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** Text shown with an edit button; saving calls onSave. */
export function EditableText(props: {
  value: string | null;
  placeholder: string;
  maxLength: number;
  onSave: (v: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(props.value ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canEdit = useCanEdit();

  if (!canEdit) return <span>{props.value || <span className="muted">{props.placeholder}</span>}</span>;
  if (!editing) {
    return (
      <span className="inline-edit">
        {props.value || <span className="muted">{props.placeholder}</span>}
        <button title="Rename" onClick={() => { setDraft(props.value ?? ""); setEditing(true); }}>✎</button>
      </span>
    );
  }
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await props.onSave(draft.trim());
      setEditing(false);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="inline-edit">
      <input
        autoFocus value={draft} maxLength={props.maxLength} placeholder={props.placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
      />
      <button className="primary" disabled={busy} onClick={save}>Save</button>
      <button onClick={() => setEditing(false)}>Cancel</button>
      {err && <span className="error">{err}</span>}
    </span>
  );
}
