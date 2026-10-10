import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useApiQuery } from "./useApiQuery";

describe("useApiQuery", () => {
  it("loads data and reports loading, then the result", async () => {
    const fetcher = vi.fn().mockResolvedValue("hello");
    const { result } = renderHook(() => useApiQuery("k", fetcher));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.data).toBe("hello"));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("reports an error and keeps the previous data when a later load fails", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("first")
      .mockRejectedValueOnce(new Error("boom"));
    const { result } = renderHook(() => useApiQuery("k", fetcher));
    await waitFor(() => expect(result.current.data).toBe("first"));

    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error?.message).toBe("boom"));
    expect(result.current.data).toBe("first");
    expect(result.current.loading).toBe(false);
  });

  it("refetches when the key changes and ignores the stale answer", async () => {
    let resolveSlow: (value: string) => void = () => {};
    let calls = 0;
    const fetcher = vi.fn((signal: AbortSignal) => {
      void signal;
      calls += 1;
      if (calls === 1) {
        return new Promise<string>((resolve) => {
          resolveSlow = resolve;
        });
      }
      return Promise.resolve("fast");
    });
    const { result, rerender } = renderHook(
      ({ k }) => useApiQuery(k, fetcher),
      { initialProps: { k: "a" } },
    );
    rerender({ k: "b" });
    await waitFor(() => expect(result.current.data).toBe("fast"));
    await act(async () => resolveSlow("slow"));
    expect(result.current.data).toBe("fast");
    expect(fetcher.mock.calls[0]?.[0].aborted).toBe(true);
  });

  it("does not fetch while disabled", () => {
    const fetcher = vi.fn().mockResolvedValue("x");
    const { result } = renderHook(() =>
      useApiQuery("k", fetcher, { enabled: false }),
    );
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });
});
