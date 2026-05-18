import { DISPOSITION_CODES } from "@/constants/dispositionCodes";
import { FilterAttribute } from "@/types/filters";

// Shared filter attribute definitions
export const baseFilterAttributes: Record<string, Omit<FilterAttribute, "id">> = {
  dateRange: {
    type: "dateRange",
    label: "Date and Time Range",
    config: {
      maxRangeDays: 30,
      datePresets: ["today", "yesterday", "last7days", "last30days"],
    },
  },
  dispositionCode: {
    type: "multiSelect",
    label: "Disposition Code",
    config: {
      options: [...DISPOSITION_CODES], // Use centralized disposition codes
      searchable: true,
      maxSelections: 10,
      showSelectAll: true,
    },
  },
  duration: {
    type: "numberRange",
    label: "Call Duration",
    config: {
      min: 0,
      max: 86400,
      step: 1,
      unit: "seconds",
      numberPresets: [
        { label: "< 1 min", min: 0, max: 60 },
        { label: "1-5 min", min: 60, max: 300 },
        { label: "> 5 min", min: 300, max: 86400 },
      ],
    },
  },
  status: {
    type: "radio",
    label: "Completion Status",
    config: {
      radioOptions: [
        { label: "Completed", value: "completed" },
        { label: "In Progress", value: "in_progress" },
        { label: "All", value: "all" },
      ],
      defaultValue: "all",
    },
  },
  callTags: {
    type: "tags",
    label: "Tags",
    config: {
      placeholder: "Enter tags",
    },
  },
  tokenUsage: {
    type: "numberRange",
    label: "Token Usage",
    config: {
      min: 0,
      max: 10000,
      step: 0.01,
      unit: "tokens",
    },
  },
  runId: {
    type: "number",
    label: "Workflow Run ID",
    config: {
      placeholder: "Enter run ID",
      min: 1,
      max: 9999999,
      step: 1,
    },
  },
  workflowId: {
    type: "number",
    label: "Workflow ID",
    config: {
      placeholder: "Enter workflow ID",
      min: 1,
      max: 999999,
      step: 1,
    },
  },
  callerNumber: {
    type: "text",
    label: "Caller Number",
    config: {
      placeholder: "Enter caller number (partial match)",
      maxLength: 20,
    },
  },
  calledNumber: {
    type: "text",
    label: "Called Number",
    config: {
      placeholder: "Enter called number (partial match)",
      maxLength: 20,
    },
  },
  campaignId: {
    type: "number",
    label: "Campaign ID",
    config: {
      placeholder: "Enter campaign ID",
      min: 1,
      max: 9999999,
      step: 1,
    },
  },
};

// Helper function to create filter attributes with proper IDs
export function createFilterAttributes(
  attributeKeys: string[],
  overrides?: Record<string, Partial<Omit<FilterAttribute, "id">>>
): FilterAttribute[] {
  return attributeKeys.map((key) => {
    const baseAttr = baseFilterAttributes[key];
    if (!baseAttr) {
      throw new Error(`Unknown filter attribute key: ${key}`);
    }

    const override = overrides?.[key] || {};

    return {
      id: key,
      ...baseAttr,
      ...override,
      config: {
        ...baseAttr.config,
        ...(override.config || {}),
      },
    };
  });
}

// Default workflow filter attributes
export const workflowFilterAttributes = createFilterAttributes([
  "dateRange",
  "dispositionCode",
  "duration",
  "status",
  "tokenUsage",
]);

// Superadmin filter attributes (includes additional fields)
export const superadminFilterAttributes = createFilterAttributes(
  [
    "dateRange",
    "runId",
    "workflowId",
    "callerNumber",
    "calledNumber",
    "dispositionCode",
    "status",
    "duration",
    "tokenUsage",
    "callTags",
  ],
  {
    // dispositionCode uses the default DISPOSITION_CODES from baseFilterAttributes
  }
);

// Usage page filter attributes (simplified for regular users)
export const usageFilterAttributes = createFilterAttributes(
  [
    "dateRange",
    "duration",
    "dispositionCode",
    "callerNumber",
    "calledNumber",
    "runId",
    "workflowId",
    "campaignId",
  ],
  {
    runId: {
      label: "Run ID",
    },
    workflowId: {
      label: "Agent ID",
    },
    dateRange: {
      label: "Date Range",
      config: {
        maxRangeDays: 90,
        datePresets: ["today", "yesterday", "last7days", "last30days"],
      },
    },
    dispositionCode: {
      label: "Disposition",
      config: {
        options: [...DISPOSITION_CODES], // Use centralized disposition codes
        searchable: false,
        maxSelections: 5,  // Allow multiple selections
        showSelectAll: true,  // Show select all option
      },
    },
    duration: {
      label: "Duration",
      config: {
        min: 0,
        max: 3600, // Up to 1 hour
        step: 1,
        unit: "seconds",
        numberPresets: [
          { label: "< 30 sec", min: 0, max: 30 },
          { label: "30 sec - 1 min", min: 30, max: 60 },
          { label: "1-3 min", min: 60, max: 180 },
          { label: "3-5 min", min: 180, max: 300 },
          { label: "> 5 min", min: 300, max: 3600 },
        ],
      },
    },
  }
);
