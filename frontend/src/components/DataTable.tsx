import type { ReactNode } from "react";
import { useMemo, useState } from "react";

export interface Column<T> {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
  /** Value used for sorting; column is sortable when provided. */
  sort?: (row: T) => number | string | null;
  num?: boolean;
}

/** Compact sortable table. */
export function DataTable<T>(props: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T, i: number) => string;
  empty?: string;
  initialSort?: { key: string; desc: boolean };
  onRowClick?: (row: T) => void;
}) {
  const [sort, setSort] = useState(props.initialSort);
  const rows = useMemo(() => {
    const col = props.columns.find((c) => c.key === sort?.key);
    if (!col?.sort || !sort) return props.rows;
    const get = col.sort;
    return [...props.rows].sort((a, b) => {
      const va = get(a), vb = get(b);
      if (va === vb) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      const r = va < vb ? -1 : 1;
      return sort.desc ? -r : r;
    });
  }, [props.rows, props.columns, sort]);

  if (props.rows.length === 0) return <div className="empty">{props.empty ?? "No data in this window."}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {props.columns.map((c) => (
              <th
                key={c.key}
                className={(c.num ? "num " : "") + (c.sort ? "sortable" : "")}
                onClick={c.sort ? () => setSort({ key: c.key, desc: sort?.key === c.key ? !sort.desc : true }) : undefined}
              >
                {c.label}
                {sort?.key === c.key ? (sort.desc ? " ▾" : " ▴") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={props.rowKey(r, i)} className={props.onRowClick ? "clickable" : undefined}
              onClick={props.onRowClick ? () => props.onRowClick?.(r) : undefined}>
              {props.columns.map((c) => (
                <td key={c.key} className={c.num ? "num" : ""}>{c.render(r)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Percentage cell with a proportional bar. */
export function PercentBar({ percent }: { percent: number }) {
  return (
    <div className="pct">
      <div className="bar" style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
      <span>{percent >= 10 ? percent.toFixed(0) : percent.toFixed(1)} %</span>
    </div>
  );
}
