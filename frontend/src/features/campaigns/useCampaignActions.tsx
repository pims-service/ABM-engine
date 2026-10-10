"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useState } from "react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAccess } from "@/features/access/AccessProvider";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { errorMessage } from "@/lib/format";

import {
  activateCampaign,
  archiveCampaign,
  type Campaign,
  campaignEditPath,
  cloneCampaign,
  restoreCampaign,
} from "./api";

export interface CampaignNotice {
  tone: "success" | "danger";
  text: string;
}

export interface CampaignActions {
  /** Copy the campaign, then open the copy's edit page. */
  clone: (campaign: Campaign) => Promise<void>;
  activate: (campaign: Campaign) => Promise<void>;
  /** Ask for confirmation, then archive. */
  archive: (campaign: Campaign) => void;
  restore: (campaign: Campaign) => void;
  /** Id of the campaign an action is running for (disable its buttons). */
  busyId: string | null;
  /** Result of the last action, for a status line. */
  notice: CampaignNotice | null;
  /** Render this once next to the screen's content: it holds the confirmation dialogs. */
  dialogs: ReactNode;
}

/**
 * Row actions for campaigns, shared by the campaign list and the client detail page: clone (then
 * navigates to the new campaign's edit page), activate, and archive/restore with a confirmation.
 * `onChanged` runs after every successful change (reload your data); the header switcher is
 * refreshed for you. A 403 hides the level's actions from then on (see `useAccess`).
 */
export function useCampaignActions(onChanged: () => void): CampaignActions {
  const router = useRouter();
  const access = useAccess();
  const refreshSelection = useOptionalSelection()?.refresh;
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<CampaignNotice | null>(null);
  const [confirming, setConfirming] = useState<{
    kind: "archive" | "restore";
    campaign: Campaign;
  } | null>(null);

  function changed() {
    refreshSelection?.();
    onChanged();
  }

  function failure(
    error: unknown,
    campaign: Campaign,
    level: "edit" | "manage",
  ) {
    const denied = access.noteError(error, level, campaign.client);
    setNotice({
      tone: "danger",
      text: denied
        ? "You do not have permission to do this."
        : errorMessage(error),
    });
  }

  async function run(
    campaign: Campaign,
    level: "edit" | "manage",
    action: () => Promise<void>,
  ) {
    setBusyId(campaign.id);
    setNotice(null);
    try {
      await action();
    } catch (error) {
      failure(error, campaign, level);
    } finally {
      setBusyId(null);
    }
  }

  async function confirm(kind: "archive" | "restore", campaign: Campaign) {
    try {
      if (kind === "archive") await archiveCampaign(campaign.id);
      else await restoreCampaign(campaign.id);
    } catch (error) {
      if (access.noteError(error, "manage", campaign.client)) {
        throw new Error("You do not have permission to do this.");
      }
      throw error; // ConfirmDialog shows the message and stays open
    }
    setConfirming(null);
    setNotice({
      tone: "success",
      text: `${kind === "archive" ? "Archived" : "Restored"} ${campaign.name}.`,
    });
    changed();
  }

  const dialogs = (
    <>
      <ConfirmDialog
        open={confirming?.kind === "archive"}
        title={`Archive ${confirming?.campaign.name ?? "campaign"}?`}
        description="It leaves the default lists and becomes read-only. Its rules and history are kept, and an admin can restore it later."
        confirmLabel="Archive campaign"
        onConfirm={() =>
          confirming?.kind === "archive"
            ? confirm("archive", confirming.campaign)
            : Promise.resolve()
        }
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        open={confirming?.kind === "restore"}
        title={`Restore ${confirming?.campaign.name ?? "campaign"}?`}
        description="It returns to the default lists and can be edited again."
        confirmLabel="Restore campaign"
        tone="primary"
        onConfirm={() =>
          confirming?.kind === "restore"
            ? confirm("restore", confirming.campaign)
            : Promise.resolve()
        }
        onCancel={() => setConfirming(null)}
      />
    </>
  );

  return {
    clone: (campaign) =>
      run(campaign, "edit", async () => {
        const copy = await cloneCampaign(campaign.id);
        refreshSelection?.();
        router.push(campaignEditPath(copy.id));
      }),
    activate: (campaign) =>
      run(campaign, "edit", async () => {
        await activateCampaign(campaign.id);
        setNotice({ tone: "success", text: `Activated ${campaign.name}.` });
        changed();
      }),
    archive: (campaign) => {
      setNotice(null);
      setConfirming({ kind: "archive", campaign });
    },
    restore: (campaign) => {
      setNotice(null);
      setConfirming({ kind: "restore", campaign });
    },
    busyId,
    notice,
    dialogs,
  };
}
