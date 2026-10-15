/**
 * CSV import of companies (Brief section 4B). All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type CsvPreview = components["schemas"]["CsvPreview"];
export type CsvPreviewRow = components["schemas"]["CsvPreviewRow"];
export type ImportBatch = components["schemas"]["ImportBatch"];
export type Mapping = Record<string, string>;

/** The multipart body of the preview and the import: the file and an optional mapping. */
function upload(file: File, mapping?: Mapping) {
  return {
    body: {} as never,
    bodySerializer: () => {
      const form = new FormData();
      form.append("file", file);
      if (mapping) form.append("mapping", JSON.stringify(mapping));
      return form;
    },
  };
}

/** Read the whole file and report what an import would do. Nothing is stored. */
export async function previewCsv(
  campaignId: string,
  file: File,
  mapping?: Mapping,
): Promise<CsvPreview> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{campaign_id}/csv-import/preview/",
    { params: { path: { campaign_id: campaignId } }, ...upload(file, mapping) },
  );
  return data as CsvPreview;
}

/** Start the import (manager level). Rows are processed in the background. */
export async function startCsvImport(
  campaignId: string,
  file: File,
  mapping: Mapping,
): Promise<ImportBatch> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{campaign_id}/csv-import/",
    { params: { path: { campaign_id: campaignId } }, ...upload(file, mapping) },
  );
  return (data as { batch: ImportBatch }).batch;
}

export async function getImportBatch(
  batchId: string,
  signal?: AbortSignal,
): Promise<ImportBatch> {
  const { data } = await getApiClient().GET("/api/v1/imports/{batch_id}/", {
    params: { path: { batch_id: batchId } },
    signal,
  });
  return data as ImportBatch;
}

/** Save a text file the API generated (needs the sign-in token, so a plain link cannot do it). */
function save(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export async function downloadTemplate(): Promise<void> {
  const { data } = await getApiClient().GET("/api/v1/imports/csv-template/", {
    parseAs: "text",
  });
  save(String(data), "companies-template.csv");
}

export async function downloadProblems(batchId: string): Promise<void> {
  const { data } = await getApiClient().GET(
    "/api/v1/imports/{batch_id}/errors.csv",
    { params: { path: { batch_id: batchId } }, parseAs: "text" },
  );
  save(String(data), "rows-with-problems.csv");
}

export async function listImports(
  campaignId: string,
  signal?: AbortSignal,
): Promise<ImportBatch[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/campaigns/{campaign_id}/imports/",
    { params: { path: { campaign_id: campaignId } }, signal },
  );
  return (data as { results: ImportBatch[] }).results;
}

/** Stop an import that has not finished (manager level). Rows already added stay. */
export async function cancelImport(batchId: string): Promise<void> {
  await getApiClient().POST("/api/v1/imports/{batch_id}/cancel/", {
    params: { path: { batch_id: batchId } },
  });
}
