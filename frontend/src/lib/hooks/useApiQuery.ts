"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface ApiQueryState<T> {
  /** The latest successful result. Kept while the next one loads, so lists do not flicker. */
  data: T | undefined;
  /** The error of the latest attempt (cleared when a new attempt starts). */
  error: Error | null;
  /** True while a request is in flight (also for the first load). */
  loading: boolean;
  /** Fetch again with the same key. */
  reload: () => void;
}

/**
 * Loads data when `key` changes (and on `reload()`), ignoring stale answers and aborting the
 * request when the key changes or the component unmounts. Pass a stable string `key` that
 * identifies the request (for example `JSON.stringify(params)`); the `fetcher` itself is read
 * through a ref, so an inline arrow function is fine.
 *
 * `enabled: false` skips fetching (for example while an id is not known yet).
 */
export function useApiQuery<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: { enabled?: boolean } = {},
): ApiQueryState<T> {
  const { enabled = true } = options;
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [version, setVersion] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetcherRef
      .current(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause : new Error(String(cause)));
        setLoading(false);
      });
    return () => controller.abort();
    // `key` stands for the request parameters, `version` for an explicit reload.
  }, [key, version, enabled]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, loading, reload };
}
