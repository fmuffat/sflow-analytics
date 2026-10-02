import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView } from "../components/ui";
import { formatTime } from "../lib/format";

interface User {
  id: number; username: string; role: "admin" | "viewer"; must_change_password: boolean;
  created_at: string; last_login_at: string | null; sessions: number;
}
interface LoginEvent { at: string; username: string; client: string | null; ok: boolean; detail: string | null }

const ROLE_LABEL = { admin: "Administrator", viewer: "Read-only" };

/** Accounts (administrators only) and the sign-in log. */
export function UsersAdmin() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<{ items: User[] }>("/admin/users") });
  const [form, setForm] = useState({ username: "", role: "viewer" as User["role"] });
  const [secret, setSecret] = useState<{ username: string; password: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const done = () => { setErr(null); qc.invalidateQueries({ queryKey: ["users"] }); qc.invalidateQueries({ queryKey: ["logins"] }); };
  const fail = (e: unknown) => setErr((e as Error).message);

  const create = useMutation({
    mutationFn: () => api.post<{ username: string; password: string }>("/admin/users", form),
    onSuccess: (r) => { setSecret(r); setForm({ username: "", role: "viewer" }); done(); },
    onError: fail,
  });
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); done(); } catch (e) { fail(e); } };
  const setRole = (u: User, role: User["role"]) => act(() => api.put(`/admin/users/${encodeURIComponent(u.username)}/role`, { role }));
  const reset = (u: User) => {
    if (!window.confirm(`Generate a new password for ${u.username}? Their sessions are closed.`)) return;
    act(async () => setSecret(await api.post<{ username: string; password: string }>(`/admin/users/${encodeURIComponent(u.username)}/reset-password`)));
  };
  const remove = (u: User) => {
    if (!window.confirm(`Delete the account ${u.username}?`)) return;
    act(() => api.del(`/admin/users/${encodeURIComponent(u.username)}`));
  };

  return (
    <>
      <div className="muted" style={{ marginBottom: 10 }}>Read-only accounts see every analytics page but cannot change anything nor open Administration.</div>
      <div className="grid two">
        <Panel title="New account">
          <form className="inline-edit" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <input placeholder="user name, e.g. j.dupont" maxLength={64} value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value.trim() })} />
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as User["role"] })}>
              <option value="viewer">Read-only</option>
              <option value="admin">Administrator</option>
            </select>
            <button className="primary" disabled={form.username.length < 2 || create.isPending}>Create</button>
          </form>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            A random password is generated and shown once; the user must change it at first sign-in.
            Send it through another channel than the link (e.g. SMS or phone).
          </div>
          {err && <div className="error" style={{ padding: "6px 0 0" }}>{err}</div>}
        </Panel>
        {secret ? (
          <Panel title={`Password for ${secret.username}`} actions={<button onClick={() => setSecret(null)}>Done</button>}>
            <div className="secret-box">
              <code className="mono">{secret.password}</code>
              <button onClick={() => navigator.clipboard?.writeText(secret.password)}>Copy</button>
            </div>
            <div className="warn-text" style={{ fontSize: 12, marginTop: 8 }}>
              Shown only now. It is valid for the first sign-in only (a new password is then required).
            </div>
          </Panel>
        ) : <SignInStats />}
      </div>
      <div style={{ marginTop: 12 }}>
        <Panel title="Accounts" flush>
          <QueryView q={users}>
            {(d) => (
              <DataTable<User>
                rows={d.items} rowKey={(u) => u.username} initialSort={{ key: "name", desc: false }}
                columns={[
                  { key: "name", label: "User", render: (u) => <b>{u.username}</b>, sort: (u) => u.username.toLowerCase() },
                  { key: "role", label: "Role", render: (u) => (
                    <select value={u.role} onChange={(e) => setRole(u, e.target.value as User["role"])}>
                      {(Object.keys(ROLE_LABEL) as User["role"][]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                    </select>), sort: (u) => u.role },
                  { key: "status", label: "Status", render: (u) => u.must_change_password
                      ? <span className="badge warn">first sign-in pending</span>
                      : u.sessions ? <span className="badge ok">{u.sessions} open session{u.sessions > 1 ? "s" : ""}</span> : "" },
                  { key: "last", label: "Last sign-in", render: (u) => formatTime(u.last_login_at), sort: (u) => u.last_login_at ?? "" },
                  { key: "created", label: "Created", render: (u) => formatTime(u.created_at), sort: (u) => u.created_at },
                  { key: "act", label: "", render: (u) => (
                    <span className="inline-edit"><button onClick={() => reset(u)}>New password</button><button onClick={() => remove(u)}>Delete</button></span>) },
                ]}
              />
            )}
          </QueryView>
        </Panel>
      </div>
      <div style={{ marginTop: 12 }}>
        <SignInLog />
      </div>
    </>
  );
}

function SignInStats() {
  const q = useLogins(false);
  return (
    <Panel title="Last 24 hours">
      <QueryView q={q}>
        {(d) => (
          <dl className="kv">
            <dt>Sign-in attempts</dt><dd>{d.last_24h.total}</dd>
            <dt>Failed</dt><dd>{d.last_24h.failed ? <span className="badge warn">{d.last_24h.failed}</span> : 0}</dd>
            <dt>Addresses with failures</dt><dd>{d.last_24h.failed_clients}</dd>
          </dl>
        )}
      </QueryView>
    </Panel>
  );
}

const useLogins = (failedOnly: boolean) =>
  useQuery({
    queryKey: ["logins", failedOnly],
    queryFn: () => api.get<{ items: LoginEvent[]; last_24h: { total: number; failed: number; failed_clients: number } }>(
      "/admin/logins", undefined, { limit: 300, ...(failedOnly ? { failed_only: "true" } : {}) }),
    refetchInterval: 60_000,
  });

function SignInLog() {
  const [failed, setFailed] = useState(false);
  const q = useLogins(failed);
  return (
    <Panel
      title="Sign-in log" note="successes and failures, kept 180 days"
      actions={<span className="seg">
        <button className={!failed ? "on" : ""} onClick={() => setFailed(false)}>All</button>
        <button className={failed ? "on" : ""} onClick={() => setFailed(true)}>Failures</button>
      </span>}
      flush
    >
      <QueryView q={q}>
        {(d) => (
          <DataTable<LoginEvent>
            rows={d.items} rowKey={(e, i) => e.at + i} empty="No sign-in yet."
            columns={[
              { key: "at", label: "Time", render: (e) => formatTime(e.at, true) },
              { key: "user", label: "User name", render: (e) => e.username },
              { key: "ok", label: "Result", render: (e) => e.ok ? <span className="badge ok">signed in</span> : <span className="badge bad">{e.detail || "failed"}</span> },
              { key: "client", label: "Address", render: (e) => <span className="mono">{e.client}</span> },
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}
