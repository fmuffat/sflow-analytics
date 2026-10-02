import { Link } from "react-router-dom";
import { useTraffic } from "../hooks/queries";
import type { Filters } from "../lib/filters";
import { formatBytes } from "../lib/format";
import { DataTable, PercentBar } from "./DataTable";
import { explorerLink } from "./TopPanels";
import { Panel, QueryView } from "./ui";

interface GroupItem { grp: string; bytes: number; packets: number; samples: number; percent: number }
interface Cell { src_group: string; dst_group: string; bytes: number; percent: number }

const pick = (name: string, key: "src_group" | "dst_group"): Filters =>
  name === "Ungrouped" || name === "Non-IP" ? {} : { [key]: name };

/** Top source/destination groups and the group-to-group matrix. */
export function GroupViews(props: { filters: Filters }) {
  return (
    <>
      <div className="grid two">
        <TopGroups filters={props.filters} direction="src" />
        <TopGroups filters={props.filters} direction="dst" />
      </div>
      <Matrix filters={props.filters} />
    </>
  );
}

function TopGroups(props: { filters: Filters; direction: "src" | "dst" }) {
  const q = useTraffic<{ groups: number; items: GroupItem[] }>("top-groups", props.filters, { direction: props.direction, limit: 50 });
  const key = props.direction === "src" ? "src_group" : "dst_group";
  return (
    <Panel title={props.direction === "src" ? "Top source groups" : "Top destination groups"} flush>
      <QueryView q={q}>
        {(d) => d.groups === 0 ? <div className="empty">No IP group defined: create some in Inventory → Groups.</div> : (
          <DataTable<GroupItem>
            rows={d.items} rowKey={(r) => r.grp} initialSort={{ key: "bytes", desc: true }}
            columns={[
              { key: "grp", label: "Group", render: (r) => Object.keys(pick(r.grp, key)).length
                  ? <Link to={explorerLink(props.filters, pick(r.grp, key))}>{r.grp}</Link> : <span className="muted">{r.grp}</span>, sort: (r) => r.grp },
              { key: "bytes", label: "Est. traffic", render: (r) => formatBytes(r.bytes), sort: (r) => r.bytes, num: true },
              { key: "percent", label: "Share", render: (r) => <PercentBar percent={r.percent} />, sort: (r) => r.percent },
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

function Matrix(props: { filters: Filters }) {
  const q = useTraffic<{ groups: string[]; items: Cell[] }>("group-matrix", props.filters);
  return (
    <Panel title="Group matrix" note="rows = source group, columns = destination group · click a cell to drill down" flush>
      <QueryView q={q}>
        {(d) => {
          if (d.groups.length <= 1) return <div className="empty">No IP group defined.</div>;
          const cols = [...d.groups, "Non-IP"].filter((g) => d.items.some((c) => c.dst_group === g));
          const rows = [...d.groups, "Non-IP"].filter((g) => d.items.some((c) => c.src_group === g));
          const cell = new Map(d.items.map((c) => [c.src_group + "\u0000" + c.dst_group, c]));
          const max = Math.max(1, ...d.items.map((c) => c.bytes));
          return (
            <div className="table-wrap">
              <table className="matrix">
                <thead><tr><th>from \\ to</th>{cols.map((c) => <th key={c} className="num">{c}</th>)}</tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r}>
                      <th>{r}</th>
                      {cols.map((c) => {
                        const v = cell.get(r + "\u0000" + c);
                        const f = { ...pick(r, "src_group"), ...pick(c, "dst_group") };
                        return (
                          <td key={c} className="num" style={v ? { background: `rgba(245,130,32,${0.08 + 0.6 * (v.bytes / max)})` } : undefined}>
                            {v ? <Link to={explorerLink(props.filters, f)} title={`${v.percent} %`}>{formatBytes(v.bytes)}</Link> : <span className="muted">–</span>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      </QueryView>
    </Panel>
  );
}
