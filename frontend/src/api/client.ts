import type { Filters } from "../lib/filters";
import { toQuery } from "../lib/filters";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, contentType = "application/json"): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = contentType;
  // Marks requests coming from the web UI (CSRF defence in depth on mutations).
  if (method !== "GET") headers["X-Requested-With"] = "sflow";
  const res = await fetch(BASE + path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : contentType === "application/json" ? JSON.stringify(body) : String(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string, filters?: Filters, extra?: Record<string, string | number>) => {
    const q = toQuery({ ...(filters ?? {}), ...Object.fromEntries(Object.entries(extra ?? {}).map(([k, v]) => [k, String(v)])) });
    return request<T>("GET", path + (q ? "?" + q : ""));
  },
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  postText: <T>(path: string, text: string) => request<T>("POST", path, text, "text/csv"),
};

/** Download URL of a traffic table as CSV (same filters, up to 1000 rows). */
export const csvUrl = (path: string, filters: Filters, extra: Record<string, string> = {}) =>
  `${BASE}/traffic/${path}?` + toQuery({ ...filters, limit: "1000", ...extra, format: "csv" });

/** Exporter ids contain slashes; they are used as-is in API paths. */
export const exporterPath = (id: string) => `/exporters/${id}`;
export const interfacePath = (exporterId: string, ifindex: number) => `/interfaces/${exporterId}/${ifindex}`;
