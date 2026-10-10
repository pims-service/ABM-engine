"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type Campaign,
  getCampaign,
  listClientCampaigns,
} from "@/features/campaigns/api";
import { type Client, getClient, listClients } from "@/features/clients/api";
import { ApiError } from "@/lib/api";

import {
  EMPTY_SELECTION,
  readStoredSelection,
  type StoredSelection,
  writeStoredSelection,
} from "./storage";

/** How many clients/campaigns the switcher loads (the API's maximum page size). */
const OPTIONS_PAGE_SIZE = 100;

export interface SelectionContextValue {
  /** Id of the selected client, or null. Already validated against the API. */
  clientId: string | null;
  /** Id of the selected campaign (always one of the selected client's), or null. */
  campaignId: string | null;
  client: Client | null;
  campaign: Campaign | null;
  /** Active clients for the switcher (first page of up to 100, by name). */
  clients: Client[];
  /** Non-archived campaigns of the selected client (first page of up to 100, by name). */
  campaigns: Campaign[];
  /** True while the stored selection is being validated or the option lists are loading. */
  loading: boolean;
  /** Set when the option lists could not be loaded (the stored selection is then kept). */
  error: Error | null;
  /** Select a client (clears the campaign), or pass null to clear both. */
  selectClient: (id: string | null) => void;
  /** Select a campaign (also selects its client), or null to clear only the campaign. */
  selectCampaign: (campaign: Campaign | null) => void;
  /**
   * Reload the lists and re-validate the selection. Call it after changing clients or campaigns
   * (create, rename, archive, restore, clone) so the switcher and the context stay current.
   */
  refresh: () => void;
}

const SelectionContext = createContext<SelectionContextValue | null>(null);

/** The selected client/campaign and the switcher's data. Throws outside `SelectionProvider`. */
export function useSelection(): SelectionContextValue {
  const value = useContext(SelectionContext);
  if (!value) {
    throw new Error("useSelection must be used inside <SelectionProvider>.");
  }
  return value;
}

/** Like {@link useSelection} but null outside a provider (optional UI such as the header). */
export function useOptionalSelection(): SelectionContextValue | null {
  return useContext(SelectionContext);
}

/** The id no longer exists for this user (or is not visible): forget it. */
function isGone(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 403 || error.status === 404)
  );
}

/**
 * Holds the app-wide current client and campaign. The choice is remembered in `localStorage`
 * (key `abm-selection`, every access wrapped in try/catch) and validated against the API on load:
 * an id that is gone, archived, not visible to this user, or a campaign that does not belong to
 * the client is dropped. A network failure keeps the stored choice. Mount it inside the
 * authenticated part of the app; it fetches on mount.
 */
export function SelectionProvider({ children }: { children: ReactNode }) {
  const [selection, setSelection] = useState<StoredSelection>(EMPTY_SELECTION);
  const [clients, setClients] = useState<Client[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [clientsLoading, setClientsLoading] = useState(true);
  const [campaignsLoading, setCampaignsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [version, setVersion] = useState(0);

  const selectionRef = useRef(selection);
  const hydrated = useRef(false);

  const apply = useCallback((next: StoredSelection) => {
    selectionRef.current = next;
    setSelection(next);
    if (hydrated.current) writeStoredSelection(next);
  }, []);

  // Clients: load the options and validate the selected client.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setClientsLoading(true);
      if (!hydrated.current) {
        apply(readStoredSelection());
        hydrated.current = true;
      }
      let list: Client[];
      try {
        const page = await listClients({
          archived: "false",
          ordering: "name",
          page_size: OPTIONS_PAGE_SIZE,
        });
        list = page.results;
        if (cancelled) return;
        setClients(list);
        setError(null);
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause : new Error(String(cause)));
        setClientsLoading(false);
        return;
      }
      const { clientId } = selectionRef.current;
      if (clientId && !list.some((client) => client.id === clientId)) {
        // Not on the first page: ask for it directly (it may also be archived or gone).
        try {
          const found = await getClient(clientId);
          if (cancelled) return;
          if (found.status === "archived") apply(EMPTY_SELECTION);
          else setClients((current) => [...current, found]);
        } catch (cause) {
          if (cancelled) return;
          if (isGone(cause)) apply(EMPTY_SELECTION);
        }
      }
      if (!cancelled) setClientsLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [version, apply]);

  // Campaigns of the selected client: load the options and validate the selected campaign.
  const { clientId } = selection;
  useEffect(() => {
    if (!clientId) {
      setCampaigns([]);
      setCampaignsLoading(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      setCampaignsLoading(true);
      let list: Campaign[];
      try {
        const page = await listClientCampaigns(clientId, {
          archived: "false",
          ordering: "name",
          page_size: OPTIONS_PAGE_SIZE,
        });
        list = page.results;
        if (cancelled) return;
        setCampaigns(list);
      } catch (cause) {
        if (cancelled) return;
        if (isGone(cause)) apply(EMPTY_SELECTION);
        else
          setError(cause instanceof Error ? cause : new Error(String(cause)));
        setCampaigns([]);
        setCampaignsLoading(false);
        return;
      }
      const { campaignId } = selectionRef.current;
      if (campaignId && !list.some((campaign) => campaign.id === campaignId)) {
        try {
          const found = await getCampaign(campaignId);
          if (cancelled) return;
          if (found.client !== clientId || found.status === "archived") {
            apply({ clientId, campaignId: null });
          } else {
            setCampaigns((current) => [...current, found]);
          }
        } catch (cause) {
          if (cancelled) return;
          if (isGone(cause)) apply({ clientId, campaignId: null });
        }
      }
      if (!cancelled) setCampaignsLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [clientId, version, apply]);

  const selectClient = useCallback(
    (id: string | null) => {
      if (id === selectionRef.current.clientId) return;
      apply({ clientId: id, campaignId: null });
    },
    [apply],
  );

  const selectCampaign = useCallback(
    (campaign: Campaign | null) => {
      const current = selectionRef.current;
      if (!campaign) {
        if (current.campaignId) apply({ ...current, campaignId: null });
        return;
      }
      apply({ clientId: campaign.client, campaignId: campaign.id });
    },
    [apply],
  );

  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  const value = useMemo<SelectionContextValue>(() => {
    // After switching client the old client's campaigns are still in state until the new load.
    const ofClient = campaigns.filter(
      (item) => item.client === selection.clientId,
    );
    return {
      clientId: selection.clientId,
      campaignId: selection.campaignId,
      client: clients.find((item) => item.id === selection.clientId) ?? null,
      campaign:
        ofClient.find((item) => item.id === selection.campaignId) ?? null,
      clients,
      campaigns: ofClient,
      loading: clientsLoading || campaignsLoading,
      error,
      selectClient,
      selectCampaign,
      refresh,
    };
  }, [
    selection,
    clients,
    campaigns,
    clientsLoading,
    campaignsLoading,
    error,
    selectClient,
    selectCampaign,
    refresh,
  ]);

  return (
    <SelectionContext.Provider value={value}>
      {children}
    </SelectionContext.Provider>
  );
}
