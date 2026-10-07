import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { AccessProvider, useAccess } from "./AccessProvider";

const forbidden = new ApiError({
  status: 403,
  code: "permission_denied",
  message: "No.",
});
const wrapper = ({ children }: { children: ReactNode }) => (
  <AccessProvider>{children}</AccessProvider>
);

describe("useAccess", () => {
  it("allows everything outside a provider (the API decides)", () => {
    const { result } = renderHook(() => useAccess());
    expect(result.current.can("manage", "c1")).toBe(true);
    expect(result.current.noteError(forbidden, "edit")).toBe(true);
    expect(result.current.noteError(new Error("x"), "edit")).toBe(false);
  });

  it("starts optimistic and hides a level after a 403, per client", () => {
    const { result } = renderHook(() => useAccess(), { wrapper });
    expect(result.current.can("edit", "c1")).toBe(true);

    act(() => {
      result.current.noteError(forbidden, "edit", "c1");
    });
    expect(result.current.can("edit", "c1")).toBe(false);
    // Roles are cumulative: no EDIT means no MANAGE either.
    expect(result.current.can("manage", "c1")).toBe(false);
    expect(result.current.can("edit", "c2")).toBe(true);
  });

  it("a denied manage keeps edit available", () => {
    const { result } = renderHook(() => useAccess(), { wrapper });
    act(() => {
      result.current.noteError(forbidden, "manage", "c1");
    });
    expect(result.current.can("manage", "c1")).toBe(false);
    expect(result.current.can("edit", "c1")).toBe(true);
  });

  it("ignores errors that are not a 403", () => {
    const { result } = renderHook(() => useAccess(), { wrapper });
    act(() => {
      result.current.noteError(
        new ApiError({ status: 400, code: "validation_error", message: "bad" }),
        "edit",
        "c1",
      );
    });
    expect(result.current.can("edit", "c1")).toBe(true);
  });

  it("accepts roles known up front", () => {
    const { result } = renderHook(() => useAccess(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <AccessProvider initialDenied={[{ level: "manage" }]}>
          {children}
        </AccessProvider>
      ),
    });
    expect(result.current.can("manage")).toBe(false);
  });
});
