import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Panel, QueryView } from "./ui";

interface NotifySettings {
  public_url: string;
  email: { enabled: boolean; host: string; port: number; security: "starttls" | "ssl" | "none"; username: string; sender: string; recipients: string[]; password_set?: boolean };
  webhook: { enabled: boolean; format: "teams" | "slack" | "generic"; url_set?: boolean; url_hint?: string };
  syslog: { enabled: boolean; host: string; port: number; facility: number };
}

/** Administration → Notifications: channels used by alert rules. */
export function NotificationsPanel() {
  const q = useQuery({ queryKey: ["notify"], queryFn: () => api.get<NotifySettings>("/admin/notifications") });
  return (
    <Panel title="Notifications" note="channels used by the alert rules; secrets are stored encrypted and never shown">
      <QueryView q={q}>{(d) => <NotifyForm initial={d} />}</QueryView>
    </Panel>
  );
}

function NotifyForm({ initial }: { initial: NotifySettings }) {
  const qc = useQueryClient();
  const [s, setS] = useState(initial);
  const [recipients, setRecipients] = useState(initial.email.recipients.join(", "));
  const [password, setPassword] = useState("");
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setS(initial), [initial]);
  const email = (k: keyof NotifySettings["email"], v: unknown) => setS({ ...s, email: { ...s.email, [k]: v } });
  const save = async () => {
    setMsg(null);
    try {
      await api.put("/admin/notifications", {
        ...s, email: { ...s.email, recipients: recipients.split(/[\s,;]+/).filter(Boolean) },
        ...(password ? { smtp_password: password } : {}), ...(url ? { webhook_url: url } : {}),
      });
      setPassword(""); setUrl("");
      setMsg({ ok: true, text: "Saved." });
      qc.invalidateQueries({ queryKey: ["notify"] });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  const test = async (channel: string) => {
    setMsg(null);
    try { await api.post(`/admin/notifications/test/${channel}`); setMsg({ ok: true, text: `Test message sent (${channel}).` }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <label>Public URL of this appliance <span className="muted">(links in notifications, e.g. https://sflow.example.com:8443)</span>
        <input value={s.public_url} onChange={(e) => setS({ ...s, public_url: e.target.value })} placeholder="https://..." /></label>

      <fieldset className="notify-box">
        <legend><label className="pick"><input type="checkbox" checked={s.email.enabled} onChange={(e) => email("enabled", e.target.checked)} /> E-mail (SMTP)</label></legend>
        <div className="filters" style={{ gridTemplateColumns: "2fr 1fr 1fr" }}>
          <label>SMTP server<input value={s.email.host} onChange={(e) => email("host", e.target.value)} placeholder="smtp.office365.com" /></label>
          <label>Port<input type="number" value={s.email.port} onChange={(e) => email("port", Number(e.target.value))} /></label>
          <label>Security<select value={s.email.security} onChange={(e) => email("security", e.target.value)}>
            <option value="starttls">STARTTLS (587)</option><option value="ssl">SSL/TLS (465)</option><option value="none">none (25, internal relay)</option></select></label>
          <label>User name<input value={s.email.username} onChange={(e) => email("username", e.target.value)} autoComplete="off" /></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
            placeholder={s.email.password_set ? "•••••• (stored; type to replace)" : ""} /></label>
          <label>Sender<input value={s.email.sender} onChange={(e) => email("sender", e.target.value)} placeholder="sflow@example.com" /></label>
          <label style={{ gridColumn: "1 / -1" }}>Default recipients<input value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="noc@example.com, it@example.com" /></label>
        </div>
        <button onClick={() => test("email")}>Send a test e-mail</button>
      </fieldset>

      <fieldset className="notify-box">
        <legend><label className="pick"><input type="checkbox" checked={s.webhook.enabled} onChange={(e) => setS({ ...s, webhook: { ...s.webhook, enabled: e.target.checked } })} /> Teams / Slack (webhook)</label></legend>
        <div className="filters" style={{ gridTemplateColumns: "1fr 3fr" }}>
          <label>Format<select value={s.webhook.format} onChange={(e) => setS({ ...s, webhook: { ...s.webhook, format: e.target.value as NotifySettings["webhook"]["format"] } })}>
            <option value="teams">Microsoft Teams (Workflows)</option><option value="slack">Slack / Mattermost</option><option value="generic">generic JSON</option></select></label>
          <label>Webhook URL<input type="password" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off"
            placeholder={s.webhook.url_set ? `stored (${s.webhook.url_hint}); type to replace` : "https://..."} /></label>
        </div>
        <button onClick={() => test("webhook")}>Send a test message</button>
      </fieldset>

      <fieldset className="notify-box">
        <legend><label className="pick"><input type="checkbox" checked={s.syslog.enabled} onChange={(e) => setS({ ...s, syslog: { ...s.syslog, enabled: e.target.checked } })} /> Syslog (UDP)</label></legend>
        <div className="filters" style={{ gridTemplateColumns: "2fr 1fr 1fr" }}>
          <label>Server<input value={s.syslog.host} onChange={(e) => setS({ ...s, syslog: { ...s.syslog, host: e.target.value } })} placeholder="192.168.1.10" /></label>
          <label>Port<input type="number" value={s.syslog.port} onChange={(e) => setS({ ...s, syslog: { ...s.syslog, port: Number(e.target.value) } })} /></label>
          <label>Facility<select value={s.syslog.facility} onChange={(e) => setS({ ...s, syslog: { ...s.syslog, facility: Number(e.target.value) } })}>
            {[16, 17, 18, 19, 20, 21, 22, 23].map((f) => <option key={f} value={f}>local{f - 16}</option>)}</select></label>
        </div>
        <button onClick={() => test("syslog")}>Send a test message</button>
      </fieldset>

      <span className="inline-edit">
        <button className="primary" onClick={save}>Save notifications</button>
        <span className="muted">save before testing</span>
        {msg && <span className={msg.ok ? "muted" : "error"} style={{ padding: 0 }}>{msg.text}</span>}
      </span>
    </div>
  );
}
