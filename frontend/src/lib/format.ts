// Formatting helpers. Network units are decimal (1 kB = 1000 B, 1 Mb/s = 10^6 bit/s).

const UNITS = ["", "k", "M", "G", "T", "P"];

function scaled(value: number, suffix: string, digits = 1): string {
  if (!Number.isFinite(value)) return "–";
  let v = Math.abs(value);
  let i = 0;
  while (v >= 1000 && i < UNITS.length - 1) {
    v /= 1000;
    i++;
  }
  const d = i === 0 ? 0 : v >= 100 ? 0 : digits;
  return `${value < 0 ? "-" : ""}${v.toFixed(d)} ${UNITS[i]}${suffix}`;
}

export const formatBytes = (b: number) => scaled(b, "B");
export const formatBps = (bps: number) => scaled(bps, "b/s");
export const formatPps = (pps: number) => (pps < 10 ? `${pps.toFixed(2)} p/s` : scaled(pps, "p/s"));
export const formatSpeed = (bps: number | null | undefined) => (bps ? scaled(bps, "b/s", 0).replace(" ", "") : "–");

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return Math.round(n).toLocaleString("en-US");
}

export function formatRate(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return n >= 100 ? formatCount(n) : n.toFixed(n >= 10 ? 1 : 2);
}

export const formatPercent = (p: number) => (p >= 10 ? p.toFixed(0) : p.toFixed(1)) + " %";

// Clock preference (24 h or 12 h AM/PM), per browser. Default: 24 h.
export type Clock = "24h" | "12h";
const CLOCK_KEY = "sflow.clock";

function readClock(): Clock {
  try {
    return localStorage.getItem(CLOCK_KEY) === "12h" ? "12h" : "24h";
  } catch {
    return "24h";
  }
}

let clock: Clock = readClock();

export const getClock = (): Clock => clock;

export function setClock(c: Clock): void {
  clock = c;
  try {
    localStorage.setItem(CLOCK_KEY, c);
  } catch {
    /* storage unavailable: preference kept for this page only */
  }
}

const hourOpts = (): Intl.DateTimeFormatOptions => (clock === "12h" ? { hour12: true } : { hourCycle: "h23" });

export function formatTime(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toLocaleString(undefined, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined, ...hourOpts(),
  });
}

/** Chart tick label: time only for short windows, date + time otherwise. */
export function formatTick(iso: string, spanSeconds: number): string {
  const d = new Date(iso);
  if (spanSeconds <= 86400) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", ...hourOpts() });
  return d.toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", ...hourOpts() });
}

export function formatAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function formatDuration(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m} min`;
}

/** ISO string -> value for <input type="datetime-local"> in local time. */
export function toLocalInput(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** <input type="datetime-local"> value (local time) -> ISO UTC string. */
export function fromLocalInput(v: string): string | undefined {
  if (!v) return undefined;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString().replace(".000Z", "Z");
}
