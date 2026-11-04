import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isStaleBuildError } from "@/lib/staleBuild";

import ErrorPage from "./error";

const reload = vi.fn();

beforeEach(() => {
  reload.mockReset();
  window.sessionStorage.clear();
  vi.stubGlobal("location", { ...window.location, reload });
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function chunkError(): Error {
  const error = new Error("Loading chunk 5742 failed.");
  error.name = "ChunkLoadError";
  return error;
}

describe("the error page", () => {
  it("recognises an old page asking for scripts that were replaced", () => {
    expect(isStaleBuildError(chunkError())).toBe(true);
    expect(
      isStaleBuildError(
        new Error("Failed to fetch dynamically imported module: /_next/x.js"),
      ),
    ).toBe(true);
    expect(isStaleBuildError(new Error("Cannot read properties of null"))).toBe(
      false,
    );
  });

  it("reloads once by itself after an update, and offers a Reload button", async () => {
    render(<ErrorPage error={chunkError()} reset={vi.fn()} />);
    expect(screen.getByText("The app was updated")).toBeInTheDocument();
    expect(reload).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("does not reload on its own twice in a row", () => {
    render(<ErrorPage error={chunkError()} reset={vi.fn()} />);
    render(<ErrorPage error={chunkError()} reset={vi.fn()} />);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("keeps the usual page and Try again for any other error", async () => {
    const reset = vi.fn();
    render(<ErrorPage error={new Error("boom")} reset={reset} />);
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
