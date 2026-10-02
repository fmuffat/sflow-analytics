import { type ChangeEvent, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Panel, QueryView } from "./ui";
import { formatTime } from "../lib/format";

interface CertInfo {
  installed: boolean; subject?: string; issuer?: string; self_signed?: boolean; names?: string[];
  not_before?: string; not_after?: string; days_left?: number; expired?: boolean; key?: string; sha256?: string;
  chain_length?: number; pending_csr: { subject: string; pem: string } | null; default_names: string[];
  warnings?: string[];
}

const readText = (f: File) => f.text();
const readBase64 = async (f: File) => {
  const bytes = new Uint8Array(await f.arrayBuffer());
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
};

/** Administration → HTTPS certificate: current certificate, import (PFX or PEM), CSR, self-signed. */
export function CertificatePanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["certificate"], queryFn: () => api.get<CertInfo>("/admin/certificate") });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const done = (r: CertInfo, what: string) => {
    const w = r.warnings?.length ? ` Note: ${r.warnings.join("; ")}.` : "";
    setMsg({ ok: true, text: `${what} nginx applies it within about 10 seconds, without interruption; then reload this page.${w}` });
    qc.invalidateQueries({ queryKey: ["certificate"] });
  };
  const fail = (e: unknown) => setMsg({ ok: false, text: (e as Error).message });

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <Panel title="Current certificate" note="used by the web interface and the API (HTTPS)">
        <QueryView q={q}>
          {(c) => c.installed ? (
            <dl className="kv">
              <dt>Issued to</dt><dd><b>{c.subject}</b></dd>
              <dt>Issued by</dt><dd>{c.issuer} {c.self_signed && <span className="badge warn">self-signed: browsers show a warning</span>}</dd>
              <dt>Names</dt><dd className="mono">{c.names?.join(", ") || "–"}</dd>
              <dt>Valid until</dt><dd>{formatTime(c.not_after)}{" "}
                {c.expired ? <span className="badge bad">expired</span>
                  : (c.days_left ?? 0) < 30 ? <span className="badge warn">{c.days_left} days left</span>
                  : <span className="muted">({c.days_left} days)</span>}</dd>
              <dt>Key</dt><dd>{c.key}{c.chain_length && c.chain_length > 1 ? ` · chain of ${c.chain_length} certificates` : ""}</dd>
              <dt>SHA-256</dt><dd className="mono" style={{ fontSize: 11, wordBreak: "break-all" }}>{c.sha256}</dd>
            </dl>
          ) : <div className="empty">No certificate found.</div>}
        </QueryView>
        {msg && <div className={msg.ok ? "notice ok" : "error"} style={{ marginTop: 8 }}>{msg.text}</div>}
      </Panel>
      <div className="grid two">
        <ImportPanel pending={!!q.data?.pending_csr} onDone={(r) => done(r, "Certificate installed.")} onError={fail} />
        <CsrPanel info={q.data} onDone={() => { setMsg({ ok: true, text: "CSR created: send it to your certificate authority, then import the signed certificate (PEM, without key)." }); qc.invalidateQueries({ queryKey: ["certificate"] }); }} onError={fail} />
      </div>
      <Panel title="Self-signed certificate" note="for tests, or to start over">
        <SelfSigned info={q.data} onDone={(r) => done(r, "New self-signed certificate installed.")} onError={fail} />
      </Panel>
    </div>
  );
}

function ImportPanel(props: { pending: boolean; onDone: (r: CertInfo) => void; onError: (e: unknown) => void }) {
  const [mode, setMode] = useState<"pfx" | "pem">("pfx");
  const [pfx, setPfx] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [cert, setCert] = useState("");
  const [key, setKey] = useState("");
  const [chain, setChain] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const r = mode === "pfx"
        ? await api.post<CertInfo>("/admin/certificate/pfx", { pfx_base64: await readBase64(pfx as File), password })
        : await api.post<CertInfo>("/admin/certificate/pem", { certificate: cert, private_key: key || null, chain: chain || null });
      setPassword(""); setKey(""); props.onDone(r);
    } catch (e) { props.onError(e); } finally { setBusy(false); }
  };
  const fileTo = (set: (s: string) => void) => (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; if (f) readText(f).then(set); e.target.value = "";
  };
  return (
    <Panel title="Install a certificate" note="checked before use: the key must match, dates and names are verified">
      <div className="form-stack">
        <span className="seg">
          <button type="button" className={mode === "pfx" ? "on" : ""} onClick={() => setMode("pfx")}>PFX / P12 file</button>
          <button type="button" className={mode === "pem" ? "on" : ""} onClick={() => setMode("pem")}>PEM (.crt + .key)</button>
        </span>
        {mode === "pfx" ? (
          <>
            <label>File (.pfx, .p12: certificate, private key and chain)
              <input type="file" accept=".pfx,.p12" onChange={(e) => setPfx(e.target.files?.[0] ?? null)} /></label>
            <label>Password of the file
              <input type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          </>
        ) : (
          <>
            <label>Certificate (PEM) <input type="file" accept=".crt,.pem,.cer" onChange={fileTo(setCert)} />
              <textarea className="mono" rows={4} value={cert} onChange={(e) => setCert(e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" /></label>
            <label>Private key (PEM){props.pending && <span className="muted"> — leave empty: the key of the CSR made here is used</span>}
              <input type="file" accept=".key,.pem" onChange={fileTo(setKey)} />
              <textarea className="mono" rows={3} value={key} onChange={(e) => setKey(e.target.value)} placeholder="-----BEGIN PRIVATE KEY-----" autoComplete="off" /></label>
            <label>Intermediate certificates (optional) <input type="file" accept=".crt,.pem,.cer" onChange={fileTo(setChain)} />
              <textarea className="mono" rows={3} value={chain} onChange={(e) => setChain(e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" /></label>
          </>
        )}
        <span className="inline-edit">
          <button className="primary" disabled={busy || (mode === "pfx" ? !pfx : !cert.trim())} onClick={run}>Install</button>
          <span className="muted">The private key is stored on the appliance only and never shown again.</span>
        </span>
      </div>
    </Panel>
  );
}

function CsrPanel(props: { info?: CertInfo; onDone: () => void; onError: (e: unknown) => void }) {
  const defaults = (props.info?.default_names ?? []).filter((n) => n !== "localhost").join("\n");
  const [names, setNames] = useState<string | null>(null);
  const [org, setOrg] = useState("");
  const [country, setCountry] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = props.info?.pending_csr;
  const run = async () => {
    setBusy(true);
    try {
      await api.post("/admin/certificate/csr", { names: (names ?? defaults).split(/[\s,;]+/).filter(Boolean), organization: org, country });
      props.onDone();
    } catch (e) { props.onError(e); } finally { setBusy(false); }
  };
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([pending?.pem ?? ""], { type: "application/pkcs10" }));
    a.download = `${pending?.subject ?? "sflow-analytics"}.csr`;
    a.click();
  };
  return (
    <Panel title="Certificate signing request (CSR)" note="for a company or customer certificate authority">
      <div className="form-stack">
        <label>Host names and IP addresses of the appliance (one per line; the first one is the main name)
          <textarea className="mono" rows={3} value={names ?? defaults} onChange={(e) => setNames(e.target.value)} placeholder={"sflow.corp.example\nIP:192.168.1.50"} /></label>
        <span className="inline-edit">
          <input placeholder="Organization (optional)" value={org} onChange={(e) => setOrg(e.target.value)} />
          <input placeholder="Country, e.g. FR" maxLength={2} style={{ width: 120 }} value={country} onChange={(e) => setCountry(e.target.value.toUpperCase())} />
          <button className="primary" disabled={busy} onClick={run}>Generate CSR</button>
        </span>
        {pending && (
          <div>
            <div className="inline-edit" style={{ marginBottom: 6 }}>
              <span className="badge warn">pending: {pending.subject}</span>
              <button onClick={() => navigator.clipboard?.writeText(pending.pem)}>Copy</button>
              <button onClick={download}>Download .csr</button>
            </div>
            <textarea className="mono" rows={6} readOnly value={pending.pem} style={{ width: "100%" }} />
            <div className="muted" style={{ fontSize: 12 }}>The private key stays on the appliance. Import the signed certificate with "PEM", without key.</div>
          </div>
        )}
      </div>
    </Panel>
  );
}

function SelfSigned(props: { info?: CertInfo; onDone: (r: CertInfo) => void; onError: (e: unknown) => void }) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!window.confirm("Replace the current certificate by a new self-signed one?")) return;
    setBusy(true);
    try { props.onDone(await api.post<CertInfo>("/admin/certificate/self-signed", {})); }
    catch (e) { props.onError(e); } finally { setBusy(false); }
  };
  return (
    <span className="inline-edit">
      <button disabled={busy} onClick={run}>Generate a new self-signed certificate</button>
      <span className="muted">for {props.info?.default_names.join(", ")}</span>
    </span>
  );
}
