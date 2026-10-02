import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, exporterPath, interfacePath } from "../api/client";
import type {
  CollectorStatus, Envelope, Exporter, FlowItem, Interface, Summary, SystemStatus, Timeseries,
  GroupedTimeseries, UtilizationItem, UtilizationSeries,
} from "../api/types";
import type { Filters } from "../lib/filters";
import { isLive } from "../lib/filters";

const LIVE_REFRESH_MS = 30_000;

/** Traffic query; refreshed automatically while the window ends at "now". */
export function useTraffic<T>(path: string, filters: Filters, extra: Record<string, string | number> = {}, enabled = true) {
  return useQuery({
    queryKey: ["traffic", path, filters, extra],
    queryFn: () => api.get<T>("/traffic/" + path, filters, extra),
    refetchInterval: isLive(filters) ? LIVE_REFRESH_MS : false,
    placeholderData: (prev) => prev,
    enabled,
  });
}

export const useTop = <T,>(path: string, filters: Filters, limit = 10, extra: Record<string, string | number> = {}) =>
  useTraffic<Envelope<T>>(path, filters, { limit, ...extra });

export const useTimeseries = (filters: Filters, enabled = true) =>
  useTraffic<Timeseries>("timeseries", filters, {}, enabled);

export const useGroupedTimeseries = (filters: Filters, groupBy: string, top: number) =>
  useTraffic<GroupedTimeseries>("timeseries", filters, { group_by: groupBy, top }, groupBy !== "");

export const useSummary = (filters: Filters) =>
  useTraffic<{ summary: Summary; seconds: number }>("summary", filters);

export const useFlows = (filters: Filters, limit: number, offset: number) =>
  useTraffic<Envelope<FlowItem>>("flows", filters, { limit, offset });

export const useExporters = () =>
  useQuery({
    queryKey: ["exporters"],
    queryFn: () => api.get<{ items: Exporter[] }>("/exporters"),
    refetchInterval: LIVE_REFRESH_MS,
  });

export const useExporter = (id: string) =>
  useQuery({
    queryKey: ["exporter", id],
    queryFn: () => api.get<Exporter>(exporterPath(id)),
    refetchInterval: LIVE_REFRESH_MS,
  });

export const useInterfaces = (exporter?: string) =>
  useQuery({
    queryKey: ["interfaces", exporter ?? ""],
    queryFn: () => api.get<{ items: Interface[] }>("/interfaces", undefined, exporter ? { exporter } : {}),
  });

export const useInterface = (exporterId: string, ifindex: number) =>
  useQuery({
    queryKey: ["interface", exporterId, ifindex],
    queryFn: () => api.get<Interface>(interfacePath(exporterId, ifindex)),
  });

export const useCollectorStatus = () =>
  useQuery({
    queryKey: ["collector-status"],
    queryFn: () => api.get<CollectorStatus>("/collector/status"),
    refetchInterval: 10_000,
  });

export const useSystemStatus = () =>
  useQuery({
    queryKey: ["system-status"],
    queryFn: () => api.get<SystemStatus>("/system/status"),
    refetchInterval: LIVE_REFRESH_MS,
  });

/** Rename an exporter or edit its notes; refreshes every view that shows names. */
export function useUpdateExporter(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { display_name?: string; notes?: string }) => api.patch<Exporter>(exporterPath(id), body),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useUpdateInterface(exporterId: string, ifindex: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string; description?: string }) =>
      api.patch<Interface>(interfacePath(exporterId, ifindex), body),
    onSuccess: () => qc.invalidateQueries(),
  });
}

/** Interface utilization from counters (exact). Only time, exporter and ifindex filters apply. */
export function useUtilizationTop(filters: Filters, limit = 10, sort: "peak" | "p95" | "avg" | "discards" = "peak") {
  const f: Filters = { range: filters.range, from: filters.from, to: filters.to, exporter: filters.exporter, ifindex: filters.ifindex };
  return useQuery({
    queryKey: ["util-top", f, limit, sort],
    queryFn: () => api.get<{ items: UtilizationItem[] }>("/utilization/interfaces", f, { limit, sort }),
    refetchInterval: isLive(filters) ? LIVE_REFRESH_MS : false,
    placeholderData: (prev) => prev,
  });
}

export function useUtilizationSeries(filters: Filters, exporter: string, ifindex: number) {
  const f: Filters = { range: filters.range, from: filters.from, to: filters.to };
  return useQuery({
    queryKey: ["util-series", f, exporter, ifindex],
    queryFn: () => api.get<UtilizationSeries>("/utilization/timeseries", f, { exporter, ifindex }),
    refetchInterval: isLive(filters) ? LIVE_REFRESH_MS : false,
    placeholderData: (prev) => prev,
  });
}
