import { useWorkspaceStore } from "@/stores/useWorkspaceStore";
import i18n from "@/lib/i18n";
import { FileMeta } from "@/types";
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

import type {
  PreviewNodeResult,
  IndexSourcePreviewResult,
  IndexManifestEntry,
  DataFramePreviewResult,
  AggregationPreviewResult,
  SheetInfo,
} from "@/types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Helper to get a file by ID from the current workspace
export const getFileById = (fileId: string): FileMeta | undefined => {
  const state = useWorkspaceStore.getState();
  return state.currentWorkspace?.files.find((f) => f.id === fileId);
};

// Preview result utilities

/**
 * Type guards for preview results
 */
export const isIndexSourcePreview = (
  result: PreviewNodeResult,
): result is IndexSourcePreviewResult => {
  return "index_values" in result && "preview_data" in result;
};

export const isDataFramePreview = (
  result: PreviewNodeResult,
): result is DataFramePreviewResult => {
  return "dataframe_previews" in result;
};

export const isAggregationPreview = (
  result: PreviewNodeResult,
): result is AggregationPreviewResult => {
  return "aggregation_results" in result && "preview_data" in result;
};

/**
 * Format an index manifest raw value for review.
 * Quotes text so surrounding whitespace and numeric-looking text stay visible.
 */
const formatManifestRawValue = (
  value: string | number | boolean | null | undefined,
  rawType?: string,
): string => {
  if (rawType === "empty" || value === null || value === undefined) {
    return i18n.t("node.indexSourceNode.manifestEmptyValue");
  }
  if (typeof value === "string") {
    // Text is quoted so " 1001 " vs 1001 and text "1001" vs number 1001 differ visibly
    return `"${value}"`;
  }
  return String(value);
};

const formatManifestRawType = (rawType?: string): string => {
  switch (rawType) {
    case "text":
      return i18n.t("node.indexSourceNode.manifestTypeText");
    case "number":
      return i18n.t("node.indexSourceNode.manifestTypeNumber");
    case "boolean":
      return i18n.t("node.indexSourceNode.manifestTypeBoolean");
    case "datetime":
      return i18n.t("node.indexSourceNode.manifestTypeDatetime");
    default:
      return i18n.t("node.indexSourceNode.manifestTypeEmpty");
  }
};

export const getIndexManifestRows = (
  manifest: IndexManifestEntry[],
): Array<Array<string | number>> =>
  manifest.map((entry) => {
    const rawValue = formatManifestRawValue(
      entry.original_value,
      entry.skipped ? "empty" : undefined,
    );
    const status = entry.skipped
      ? i18n.t("node.indexSourceNode.manifestStatusSkipped")
      : entry.merged
        ? i18n.t("node.indexSourceNode.manifestStatusMerged")
        : i18n.t("node.indexSourceNode.manifestStatusKept");

    // 逐项列出被合并的原始形态及其归并去向，便于核对合并去向
    const mergedInto = (entry.original_values ?? [])
      .filter((original) => original.merged && original.merged_into)
      .map(
        (original) =>
          `${formatManifestRawType(original.raw_type)} ` +
          `${formatManifestRawValue(original.original_value, original.raw_type)}` +
          ` → ${original.merged_into}`,
      )
      .join(" ; ");

    return [
      rawValue,
      entry.skipped
        ? i18n.t("node.indexSourceNode.manifestSkippedIndex")
        : entry.index_value ?? "",
      entry.count,
      mergedInto,
      status,
    ];
  });

/**
 * Convert preview results to SheetInfo array for consistent UI rendering
 */
export const convertPreviewToSheets = (
  result: PreviewNodeResult,
): SheetInfo[] => {
  if (!result.success) {
    return [];
  }

  if (isIndexSourcePreview(result)) {
    const sheets: SheetInfo[] = [
      {
        sheet_name: result.preview_data.sheet_name,
        columns: result.preview_data.columns,
        data: result.preview_data.data,
      },
    ];

    // 按列提取时附带可核对的索引清单，展示被跳过与合并的条目
    if (result.index_manifest && result.index_manifest.length > 0) {
      sheets.push({
        sheet_name: i18n.t("node.indexSourceNode.manifestSheetName"),
        columns: [
          i18n.t("node.indexSourceNode.manifestOriginalValue"),
          i18n.t("node.indexSourceNode.manifestIndexValue"),
          i18n.t("node.indexSourceNode.manifestCount"),
          i18n.t("node.indexSourceNode.manifestMergedInto"),
          i18n.t("node.indexSourceNode.manifestStatus"),
        ],
        data: getIndexManifestRows(result.index_manifest),
      });
    }

    return sheets;
  }

  if (isDataFramePreview(result)) {
    return result.dataframe_previews.map((preview) => ({
      sheet_name: preview.sheet_name,
      columns: preview.columns,
      data: preview.data,
    }));
  }

  if (isAggregationPreview(result)) {
    return [
      {
        sheet_name: result.preview_data.sheet_name,
        columns: result.preview_data.columns,
        data: result.preview_data.data,
      },
    ];
  }

  return [];
};

/**
 * Get preview result metadata
 */
export const getPreviewMetadata = (
  result: PreviewNodeResult,
): Record<string, any> => {
  if (!result.success) {
    return { error: result.error };
  }

  const metadata: Record<string, any> = {
    node_id: result.node_id,
    node_type: result.node_type,
    execution_time_ms: result.execution_time_ms || 0,
  };

  if (isIndexSourcePreview(result)) {
    metadata.total_index_values = result.index_values.length;
    metadata.source_column = result.source_column;

    const summary = result.manifest_summary;
    if (summary) {
      metadata.manifest_entry_count = result.index_manifest?.length ?? 0;
      metadata.manifest_skipped_count = summary.skipped_count;
      metadata.manifest_merged_index_count = summary.merged_index_count;
    }
  }

  if (isDataFramePreview(result)) {
    metadata.preview_count = result.dataframe_previews.length;
    metadata.total_rows = result.dataframe_previews.reduce(
      (sum, preview) => sum + (preview.metadata.total_rows || 0),
      0,
    );
  }

  if (isAggregationPreview(result)) {
    metadata.aggregation_count = result.aggregation_results.length;
  }

  return metadata;
};
