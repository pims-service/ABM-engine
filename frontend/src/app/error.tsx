"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";

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
    <div role="alert">
      <PageHeader
        title="Something went wrong"
        description="An unexpected error occurred. You can try again."
        actions={<Button onClick={reset}>Try again</Button>}
      />
    </div>
  );
}
