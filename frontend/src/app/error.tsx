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
        description="Something went wrong while loading this page. Try again, and if it keeps happening, tell your admin."
        actions={<Button onClick={reset}>Try again</Button>}
      />
    </div>
  );
}
