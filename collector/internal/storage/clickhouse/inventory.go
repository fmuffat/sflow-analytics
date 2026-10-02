package clickhouse

import (
	"context"
	"fmt"
	"net/netip"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"

	"sflow-analytics/collector/internal/exporters"
)

// SaveInventory writes the current exporters and their interfaces.
// ReplacingMergeTree keeps the latest row per key.
func SaveInventory(ctx context.Context, conn driver.Conn, db string, list []exporters.Exporter, now time.Time) error {
	if len(list) == 0 {
		return nil
	}
	eb, err := conn.PrepareBatch(ctx, "INSERT INTO "+db+".exporters (id, exporter_ip, agent_ip, agent_sub_id, first_seen, last_seen, sample_rate, updated_at)")
	if err != nil {
		return err
	}
	defer eb.Abort()
	ib, err := conn.PrepareBatch(ctx, "INSERT INTO "+db+".interfaces (exporter_id, ifindex, first_seen, last_seen, speed_bps, oper_up, updated_at)")
	if err != nil {
		return err
	}
	defer ib.Abort()

	for _, e := range list {
		if err := eb.Append(e.ID, e.ExporterIP, e.AgentIP, e.AgentSubID, e.FirstSeen, e.LastSeen, e.SamplingRate, now); err != nil {
			return fmt.Errorf("exporter %s: %w", e.ID, err)
		}
		for _, it := range e.Interfaces {
			if err := ib.Append(e.ID, it.IfIndex, it.FirstSeen, it.LastSeen, it.SpeedBps, it.OperUp, now); err != nil {
				return fmt.Errorf("interface %s/%d: %w", e.ID, it.IfIndex, err)
			}
		}
	}
	if err := eb.Send(); err != nil {
		return err
	}
	if ib.Rows() == 0 {
		return nil
	}
	return ib.Send()
}

// LoadInventory reads persisted exporters with their interfaces.
func LoadInventory(ctx context.Context, conn driver.Conn, db string) ([]exporters.Exporter, error) {
	rows, err := conn.Query(ctx, "SELECT id, exporter_ip, agent_ip, agent_sub_id, first_seen, last_seen, sample_rate FROM "+db+".exporters FINAL ORDER BY id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var list []exporters.Exporter
	index := map[string]int{}
	for rows.Next() {
		var e exporters.Exporter
		var exp, agent netip.Addr
		if err := rows.Scan(&e.ID, &exp, &agent, &e.AgentSubID, &e.FirstSeen, &e.LastSeen, &e.SamplingRate); err != nil {
			return nil, err
		}
		e.ExporterIP, e.AgentIP = exp.Unmap(), agent.Unmap()
		index[e.ID] = len(list)
		list = append(list, e)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	irows, err := conn.Query(ctx, "SELECT exporter_id, ifindex, first_seen, last_seen, speed_bps, oper_up FROM "+db+".interfaces FINAL ORDER BY exporter_id, ifindex")
	if err != nil {
		return nil, err
	}
	defer irows.Close()
	for irows.Next() {
		var id string
		var it exporters.Interface
		if err := irows.Scan(&id, &it.IfIndex, &it.FirstSeen, &it.LastSeen, &it.SpeedBps, &it.OperUp); err != nil {
			return nil, err
		}
		if i, ok := index[id]; ok {
			list[i].Interfaces = append(list[i].Interfaces, it)
		}
	}
	return list, irows.Err()
}
