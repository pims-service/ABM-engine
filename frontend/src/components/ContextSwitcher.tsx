"use client";

import { Select } from "@/components/ui/Select";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";

/**
 * The header's client and campaign pickers. Changing them updates the app-wide selection
 * (`useSelection`), which is remembered between visits. Renders nothing outside a
 * `SelectionProvider`.
 */
export function ContextSwitcher() {
  const selection = useOptionalSelection();
  if (!selection) return null;
  const { clients, campaigns, clientId, campaignId, loading } = selection;

  return (
    <div
      role="group"
      aria-label="Current client and campaign"
      aria-busy={loading}
      className="flex min-w-0 items-center gap-2"
    >
      <Select
        label="Current client"
        hideLabel
        value={clientId ?? ""}
        onChange={(event) => selection.selectClient(event.target.value || null)}
        className="w-32 py-1.5 text-xs sm:w-44 sm:text-sm"
      >
        <option value="">Select client</option>
        {clients.map((client) => (
          <option key={client.id} value={client.id}>
            {client.name}
          </option>
        ))}
      </Select>
      <Select
        label="Current campaign"
        hideLabel
        value={campaignId ?? ""}
        disabled={!clientId}
        onChange={(event) =>
          selection.selectCampaign(
            campaigns.find((item) => item.id === event.target.value) ?? null,
          )
        }
        className="w-32 py-1.5 text-xs sm:w-44 sm:text-sm"
      >
        <option value="">{clientId ? "Select campaign" : "No client"}</option>
        {campaigns.map((campaign) => (
          <option key={campaign.id} value={campaign.id}>
            {campaign.name}
            {campaign.status === "draft" ? " (draft)" : ""}
          </option>
        ))}
      </Select>
    </div>
  );
}
