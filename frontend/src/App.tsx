import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type Clock, getClock, setClock } from "./lib/format";
import { RoleContext } from "./hooks/role";
import { UpdateBanner } from "./components/UpdateBanner";
import { useUpdateStatus } from "./components/Updates";
import { Alerts, useAlertCounts } from "./pages/Alerts";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api/client";
import { Admin } from "./pages/Admin";
import { Hosts } from "./pages/Hosts";
import { Groups } from "./pages/Groups";
import { Layer2 } from "./pages/Layer2";
import { Trends } from "./pages/Trends";
import { ChangePassword, Login } from "./pages/Login";
import { useSystemStatus } from "./hooks/queries";
import { Dashboard } from "./pages/Dashboard";
import { Explorer } from "./pages/Explorer";
import { Devices } from "./pages/Devices";
import { DeviceDetail } from "./pages/DeviceDetail";
import { Interfaces } from "./pages/Interfaces";
import { InterfaceDetail } from "./pages/InterfaceDetail";
import { CollectorHealth } from "./pages/CollectorHealth";

const NAV: [string, [string, string][]][] = [
  ["Monitor", [["/", "Dashboard"], ["/explorer", "Traffic Explorer"], ["/trends", "Trends"], ["/alerts", "Alerts"], ["/layer2", "Layer 2"]]],
  ["Inventory", [["/devices", "Devices"], ["/interfaces", "Interfaces"], ["/hosts", "Hosts & aliases"], ["/groups", "Groups"]]],
  ["System", [["/health", "Collector Health"], ["/admin", "Administration"]]],
];
const ADMIN_ONLY = new Set(["/admin"]);

interface Me {
  username: string;
  role: string;
  must_change_password: boolean;
  auth_enabled: boolean;
}

/** Authentication gate: login page, forced password change, then the application. */
export function App() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), retry: false, staleTime: 60_000 });
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) return <Login />;
    return <div className="login-wrap"><div className="panel login error">API unreachable: {(me.error as Error).message}</div></div>;
  }
  if (!me.data) return null;
  if (me.data.must_change_password) return <ChangePassword forced />;
  return <Shell me={me.data} />;
}

function Shell({ me }: { me: Me }) {
  const qc = useQueryClient();
  const logout = async () => {
    await api.post("/auth/logout");
    qc.clear();
    await qc.invalidateQueries({ queryKey: ["me"] });
  };
  const [clock, setClockState] = useState<Clock>(getClock());
  const changeClock = (c: Clock) => {
    setClock(c);
    setClockState(c);
  };
  const system = useSystemStatus();
  const down = system.data ? Object.entries(system.data.services).filter(([, s]) => s !== "running") : [];
  const isAdmin = me.role === "admin";
  return (
    <RoleContext.Provider value={me.role}>
    <div className="app">
      <nav className="nav">
        <div className="brand">
          <span className="mark">
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M1 12l4-5 3 3 4-6 3 4" fill="none" stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
            </svg>
          </span>
          <span>
            {system.data?.app_name ?? "sFlow Analytics"}
            <small>traffic volumes are estimates</small>
          </span>
        </div>
        {NAV.map(([section, links]) => (
          <div key={section}>
            <div className="section">{section}</div>
            {links.filter(([to]) => isAdmin || !ADMIN_ONLY.has(to)).map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/"}>{label}{to === "/alerts" && <AlertBadge />}</NavLink>
            ))}
            {section === "System" && !isAdmin && <NavLink to="/account">My account</NavLink>}
          </div>
        ))}
        <div className="foot">
          {system.isError ? (
            <><span className="dot bad" />API unreachable</>
          ) : down.length ? (
            <><span className="dot warn" />{down.map(([s]) => s).join(", ")} down</>
          ) : system.data ? (
            <><span className="dot ok" />all services running</>
          ) : null}
          <div style={{ marginTop: 4 }}>version {system.data?.version ?? "…"}{isAdmin && <NewVersionHint />}</div>
          <div className="clock" title="Time format">
            <button className={clock === "24h" ? "on" : ""} onClick={() => changeClock("24h")}>24 h</button>
            <button className={clock === "12h" ? "on" : ""} onClick={() => changeClock("12h")}>12 h</button>
          </div>
          {me.auth_enabled ? (
            <div className="user">
              <span>{me.username}{!isAdmin && <span className="badge muted" style={{ marginLeft: 6 }}>read-only</span>}</span>
              <button onClick={logout}>Sign out</button>
            </div>
          ) : (
            <div className="user warn-text">authentication disabled</div>
          )}
        </div>
      </nav>
      <main className="main" key={clock}>
        <UpdateBanner />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/explorer" element={<Explorer />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/devices/:id" element={<DeviceDetail />} />
          <Route path="/interfaces" element={<Interfaces />} />
          <Route path="/interfaces/:exporter/:ifindex" element={<InterfaceDetail />} />
          <Route path="/health" element={<CollectorHealth />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/hosts" element={<Hosts />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="/layer2" element={<Layer2 />} />
          <Route path="/trends" element={<Trends />} />
          <Route path="/users" element={<Navigate to="/admin?tab=users" replace />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/account" element={<div style={{ maxWidth: 520 }}><ChangePassword /></div>} />
          <Route path="*" element={<div className="empty">Page not found.</div>} />
        </Routes>
      </main>
    </div>
    </RoleContext.Provider>
  );
}

function AlertBadge() {
  const c = useAlertCounts();
  const n = c.data?.open ?? 0;
  if (!n) return null;
  return <span className={`nav-badge ${c.data?.critical ? "bad" : "warn"}`} title={`${n} open alert(s)`}>{n}</span>;
}

/** Administrators: discreet hint when a newer release is published on GitHub. */
function NewVersionHint() {
  const u = useUpdateStatus();
  if (!u.data?.update_available) return null;
  return <NavLink to="/admin" className="new-version" title={`Version ${u.data.latest_version} is available`}>↑ {u.data.latest_version} available</NavLink>;
}
