import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { FilterKey, Filters } from "../lib/filters";
import { fromSearchParams, toQuery, withFilter } from "../lib/filters";

/** Filters stored in the page URL (bookmarkable). */
export function useFilters() {
  const [sp, setSp] = useSearchParams();
  const filters = useMemo(() => fromSearchParams(sp), [sp]);

  const replace = useCallback(
    (next: Filters) => setSp(new URLSearchParams(toQuery(next)), { replace: false }),
    [setSp],
  );
  const set = useCallback(
    (key: FilterKey, value: string | undefined) => replace(withFilter(filters, key, value)),
    [filters, replace],
  );
  return { filters, set, replace };
}
