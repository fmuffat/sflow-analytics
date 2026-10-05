import { Fragment } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useCollectorStatus } from "../hooks/queries";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView, Tabs } from "../components/ui";
import { CertificatePanel } from "../components/Certificate";
import { UpdatesPanel } from "../components/Updates";
import { UsersAdmin } from "./Users";
import { DnsPanel } from "./Hosts";
import { ChangePassword } from "./Login";
import { EnrichmentPanel } from "../components/Enrichment";
import { NotificationsPanel } from "../components/Notifications";
import { formatBytes, formatCount, formatTime } from "../lib/format";

interface Job {
  name: string;
  interval_seconds: number;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_duration_ms: number | null;
  runs: number;
  failures: number;
}

const LABELS: Record<string, string> = {
  app_name: "Application name", app_version: "Version", app_timezone: "Timezone", retention_days: "Retention (days)",
  clickhouse_host: "ClickHouse host", clickhouse_database: "ClickHouse database", auth_enabled: "Authentication",
  admin_username: "Administrator", session_idle_minutes: "Session idle timeout (min)",
  session_max_hours: "Session maximum duration (h)", cookie_secure: "Secure cookies (HTTPS only)",
};

type AdminTab = "system" | "users" | "integrations" | "notifications" | "certificate" | "account";
const TABS: [AdminTab, string][] = [
  ["system", "System"], ["users", "Users & sign-ins"], ["integrations", "Integrations"],
  ["notifications", "Notifications"], ["certificate", "HTTPS certificate"], ["account", "My account"],
];

/** Administration, in tabs (the tab is in the URL: /admin?tab=certificate). */
export function Admin() {
  const [sp, setSp] = useSearchParams();
  const tab = (TABS.find(([k]) => k === sp.get("tab"))?.[0] ?? "system") as AdminTab;
  return (
    <>
      <div className="page-head">
        <h1>Administration</h1>
        <span className="spacer" />
        <a className="btn" href="/api/v1/admin/diagnostics" download>Download diagnostics</a>
      </div>
      <Tabs tabs={TABS} value={tab} onChange={(t) => setSp(t === "system" ? {} : { tab: t }, { replace: true })} />
      {tab === "system" && <SystemTab />}
      {tab === "users" && <UsersAdmin />}
      {tab === "integrations" && (
        <div style={{ display: "grid", gap: 12 }}>
          <EnrichmentPanel />
          <DnsPanel />
        </div>
      )}
      {tab === "notifications" && <NotificationsPanel />}
      {tab === "certificate" && <CertificatePanel />}
      {tab === "account" && <div style={{ maxWidth: 560 }}><Panel title="My account"><ChangePassword /></Panel></div>}
    </>
  );
}

function SystemTab() {
  const config = useQuery({ queryKey: ["admin-config"], queryFn: () => api.get<Record<string, unknown>>("/admin/config") });
  const jobs = useQuery({ queryKey: ["admin-jobs"], queryFn: () => api.get<{ items: Job[] }>("/admin/jobs"), refetchInterval: 30_000 });
  const collector = useCollectorStatus();
  return (
    <>
      <div style={{ marginBottom: 12 }}>
        <UpdatesPanel />
      </div>
      <div className="grid two">
        <Panel title="General" note="set in .env; restart the stack to change">
          <QueryView q={config}>
            {(c) => (
              <dl className="kv">
                {Object.entries(c).map(([k, v]) => (
                  <Fragment key={k}><dt>{LABELS[k] ?? k}</dt><dd>{typeof v === "boolean" ? (v ? "enabled" : "disabled") : String(v)}</dd></Fragment>
                ))}
              </dl>
            )}
          </QueryView>
        </Panel>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
        <Panel title="Storage">
            <QueryView q={collector}>
              {(c) => c.storage ? (
                <dl className="kv">
                  <dt>Disk</dt><dd>{c.storage.disk_used_percent} % of {formatBytes(c.storage.disk_total_bytes)} used (budget {c.storage.disk_max_usage_percent} %)</dd>
                  <dt>Database size</dt><dd>{formatBytes(c.storage.database_bytes)}</dd>
                  <dt>Flow records</dt><dd>{formatCount(c.storage.flow_records_rows)}</dd>
                  <dt>Oldest record</dt><dd>{formatTime(c.storage.oldest_flow_record)}</dd>
                  <dt>Retention</dt><dd>{c.storage.retention_days} days configured{c.storage.estimated_retention_days !== null ? `, ${c.storage.estimated_retention_days} achievable` : ""}</dd>
                </dl>
              ) : <div className="empty">Storage statistics unavailable.</div>}
            </QueryView>
          </Panel>
          <Panel title="Collector">
            <QueryView q={collector}>
              {(c) => (
                <dl className="kv">
                  <dt>Listening</dt><dd className="mono">{c.listen_address}</dd>
                  <dt>Version</dt><dd>{c.version}</dd>
                  <dt>Datagrams</dt><dd>{formatCount(c.counters.datagrams_received)}</dd>
                  <dt>DB insert failures</dt><dd>{formatCount(c.counters.db_insert_failures)}</dd>
                </dl>
              )}
            </QueryView>
          </Panel>
        </div>
      </div>
      <Panel title="Background jobs" note="run by the worker service" flush>
        <QueryView q={jobs}>
          {(d) => (
            <DataTable<Job>
              rows={d.items}
              rowKey={(r) => r.name}
              empty="No job has run yet (is the worker service running?)."
              columns={[
                { key: "name", label: "Job", render: (r) => r.name },
                { key: "every", label: "Every", render: (r) => `${Math.round(r.interval_seconds / 60)} min`, num: true },
                { key: "status", label: "Last status", render: (r) => <span className={"badge " + (r.last_status === "ok" ? "ok" : "bad")}>{r.last_status ?? "–"}</span> },
                { key: "last", label: "Last run", render: (r) => formatTime(r.last_finished_at, true) },
                { key: "dur", label: "Duration", render: (r) => (r.last_duration_ms === null ? "–" : `${r.last_duration_ms} ms`), num: true },
                { key: "detail", label: "Detail / error", render: (r) => <span className="truncate" title={r.last_error ?? ""}>{r.last_error ?? ""}</span> },
                { key: "runs", label: "Runs / failures", render: (r) => `${r.runs} / ${r.failures}`, num: true },
              ]}
            />
          )}
        </QueryView>
      </Panel>
    </>
  );
}
