import type { Connection } from "@/features/integrations/api";

import { CLIENT_A } from "./fixtures";

/** A fake secret for tests only. It must never show up in the DOM, a request URL or storage. */
export const TEST_SECRET = "YOUR_FAKE_SECRET_VALUE_1234"; // pragma: allowlist secret

export function makeConnection(
  overrides: Partial<Connection> = {},
): Connection {
  return {
    id: "e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1",
    client: CLIENT_A,
    category: "llm",
    provider: "fake",
    name: "Primary LLM",
    status: "active",
    config: { model: "fake-1" },
    has_credentials: true,
    credentials_hint: { api_key: "****1234" },
    credentials_updated_at: "2026-02-03T10:00:00Z",
    last_tested_at: null,
    last_test_status: "" as Connection["last_test_status"],
    last_test_message: "",
    archived_at: null,
    created_by: null,
    created_at: "2026-02-03T10:00:00Z",
    updated_at: "2026-02-03T10:00:00Z",
    ...overrides,
  };
}
