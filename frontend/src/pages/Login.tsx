import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { LoginArt, LogoMark } from "../components/Logo";

/** Login screen shown when the API answers 401. */
export function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { username, password });
      await qc.invalidateQueries();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
      <div className="login-art"><LoginArt /></div>
      <form className="panel login" onSubmit={submit}>
        <div className="login-brand">
          <LogoMark size={30} />
          sFlow Analytics
        </div>
        <label>Username<input autoFocus={!username} value={username} autoComplete="username" onChange={(e) => setUsername(e.target.value)} /></label>
        <label>Password<input type="password" autoFocus={!!username} value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={busy || !password}>Sign in</button>
        <div className="muted" style={{ fontSize: 11 }}>
          First start: the initial admin password is in the API log and in <code>initial-admin-password</code> on the configuration volume.
        </div>
      </form>
      </div>
    </div>
  );
}

/** Password change form; `forced` is used right after the first login. */
export function ChangePassword(props: { forced?: boolean }) {
  const qc = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== confirm) return setMsg({ ok: false, text: "The new passwords do not match." });
    try {
      await api.post("/auth/password", { current_password: current, new_password: next });
      setMsg({ ok: true, text: "Password changed. Other sessions were closed." });
      setCurrent(""); setNext(""); setConfirm("");
      await qc.invalidateQueries();
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    }
  };

  const form = (
    <form className={props.forced ? "panel login" : ""} onSubmit={submit} style={{ display: "grid", gap: 10, maxWidth: 360 }}>
      {props.forced && <div className="login-brand">Choose a new password</div>}
      {props.forced && <div className="muted">The initial password must be changed before using the application.</div>}
      <label>Current password<input type="password" value={current} autoComplete="current-password" onChange={(e) => setCurrent(e.target.value)} /></label>
      <label>New password (10 characters minimum)<input type="password" value={next} autoComplete="new-password" onChange={(e) => setNext(e.target.value)} /></label>
      <label>Confirm new password<input type="password" value={confirm} autoComplete="new-password" onChange={(e) => setConfirm(e.target.value)} /></label>
      {msg && <div className={msg.ok ? "muted" : "error"}>{msg.text}</div>}
      <button className="primary" disabled={!current || next.length < 10}>Change password</button>
    </form>
  );
  return props.forced ? <div className="login-wrap">{form}</div> : form;
}
