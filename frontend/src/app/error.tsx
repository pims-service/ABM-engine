"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <section role="alert">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="mt-1 text-sm text-gray-500">
        An unexpected error occurred. You can try again.
      </p>
      <button
        type="button"
        onClick={reset}
        className="mt-4 rounded-md bg-gray-900 px-3 py-2 text-sm text-white"
      >
        Try again
      </button>
    </section>
  );
}
