import { useQuery } from "@tanstack/react-query";

/** Build of the page currently loaded. */
export const CURRENT_BUILD = typeof __BUILD_ID__ === "string" ? __BUILD_ID__ : "dev";

/** True when the server publishes another build than the one running in this page. */
export function isNewer(served: unknown, current = CURRENT_BUILD): boolean {
  const build = (served as { build?: unknown } | null)?.build;
  return typeof build === "string" && build !== "" && build !== current;
}

async function fetchVersion(): Promise<unknown> {
  const r = await fetch("/version.json", { cache: "no-store" });
  if (!r.ok) return null;
  try {
    return await r.json(); // the dev server answers index.html: ignored
  } catch {
    return null;
  }
}

/** Banner shown when a new version was deployed while this page was open. */
export function UpdateBanner() {
  const q = useQuery({
    queryKey: ["version"], queryFn: fetchVersion,
    refetchInterval: 120_000, refetchOnWindowFocus: true, retry: false, staleTime: 60_000,
  });
  if (!isNewer(q.data)) return null;
  return (
    <div className="update-banner" role="status">
      <span>🔄 A new version of sFlow Analytics is available.</span>
      <button className="primary" onClick={() => window.location.reload()}>Reload</button>
    </div>
  );
}
