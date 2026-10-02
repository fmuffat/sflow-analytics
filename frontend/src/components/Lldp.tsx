import type { Interface } from "../api/types";

/** LLDP neighbor of an interface (from the controller), with mapping warning. */
export function LldpCell({ i }: { i: Interface }) {
  const c = i.controller_port;
  if (!c) return <span className="muted">–</span>;
  if (c.mapping_uncertain) {
    return <span className="badge warn" title={`Controller port ${c.port_id} does not match this ifIndex (speed differs)`}>mapping uncertain</span>;
  }
  if (!c.lldp_neighbor && !c.lldp_mac) return <span className="muted">none</span>;
  return <span title={[c.lldp_mac, c.lldp_port_mac].filter(Boolean).join(" · ")}>{c.lldp_neighbor ?? c.lldp_mac}</span>;
}
