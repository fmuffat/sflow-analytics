import { useState } from "react";
import type { Filters } from "../lib/filters";
import { RANGES } from "../lib/filters";
import { fromLocalInput, toLocalInput } from "../lib/format";

/** Relative ranges plus a custom absolute window. */
export function TimeRange(props: { filters: Filters; onRange: (r: string) => void; onWindow: (from: string, to: string) => void }) {
  const { filters } = props;
  const custom = !!filters.from;
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState(toLocalInput(filters.from));
  const [to, setTo] = useState(toLocalInput(filters.to) || toLocalInput(new Date().toISOString()));

  return (
    <span className="inline-edit">
      <span className="seg">
        {RANGES.map((r) => (
          <button key={r} className={!custom && filters.range === r ? "on" : ""} onClick={() => props.onRange(r)}>
            {r}
          </button>
        ))}
        <button className={custom ? "on" : ""} onClick={() => setOpen(!open)}>Custom…</button>
      </span>
      {open && (
        <>
          <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          <span className="muted">→</span>
          <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
          <button
            className="primary"
            disabled={!fromLocalInput(from) || !fromLocalInput(to)}
            onClick={() => { props.onWindow(fromLocalInput(from)!, fromLocalInput(to)!); setOpen(false); }}
          >
            Apply
          </button>
        </>
      )}
    </span>
  );
}
