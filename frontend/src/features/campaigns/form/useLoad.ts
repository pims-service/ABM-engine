"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";

export type LoadState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "forbidden"; error: ApiError }
  | { status: "not_found"; error: ApiError }
  | { status: "error"; error: unknown };

/** Run an async loader (must be a stable reference, e.g. from useCallback) on mount (and on `reload`), sorting failures into the states a page shows. */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>): {
  state: LoadState<T>;
  reload: () => void;
} {
  const [state, setState] = useState<LoadState<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: "ready", data });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 403) {
          setState({ status: "forbidden", error });
        } else if (error instanceof ApiError && error.status === 404) {
          setState({ status: "not_found", error });
        } else {
          setState({ status: "error", error });
        }
      },
    );
    return () => controller.abort();
  }, [load, attempt]);

  return { state, reload: () => setAttempt((n) => n + 1) };
}
