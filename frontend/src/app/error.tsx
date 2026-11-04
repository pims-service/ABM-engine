"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { isStaleBuildError, mayReload } from "@/lib/staleBuild";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const stale = isStaleBuildError(error);

  useEffect(() => {
    console.error(error);
    // A new version was released while this page was open: its old scripts are gone. Trying again
    // asks for the same missing files, so load the new version instead.
    if (stale && mayReload()) window.location.reload();
  }, [error, stale]);

  if (stale) {
    return (
      <div role="alert">
        <PageHeader
          title="The app was updated"
          description="A new version was released while this page was open. Reload to continue; nothing you saved was lost."
          actions={
            <Button onClick={() => window.location.reload()}>Reload</Button>
          }
        />
      </div>
    );
  }

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
