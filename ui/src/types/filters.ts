// Configuration specific to each attribute type
export interface AttributeConfig {
  // For dateRange
  maxRangeDays?: number;
  datePresets?: string[]; // e.g., ["today", "last7days", "last30days"]

  // For multiSelect
  options?: string[]; // Available options to select from
  searchable?: boolean;
  maxSelections?: number;
  showSelectAll?: boolean; // Show select all/none buttons

  // For numberRange
  min?: number;
  max?: number;
  step?: number;
  unit?: string; // e.g., "seconds", "tokens"
  numberPresets?: { label: string; min: number; max: number }[];

  // For number (single value)
  placeholder?: string;
  numberSelectLabel?: string;
  numberSelectOptions?: NumberFilterOption[];
  numberSelectOptionsLoading?: boolean;

  // For radio
  radioOptions?: { label: string; value: string }[];
  defaultValue?: string;

  // For text
  maxLength?: number;
}

export interface FilterAttribute {
  id: string;
  type: "dateRange" | "multiSelect" | "numberRange" | "number" | "numberSelect" | "radio" | "tags" | "text";
  label: string;
  field?: string; // Database field to filter on (optional, backend will resolve)
  config: AttributeConfig;
}

export interface NumberFilterOption {
  label: string;
  value: number;
}

// Type-safe value types for each filter attribute
export interface DateRangeValue {
  from: Date | null;
  to: Date | null;
}

export interface MultiSelectValue {
  codes: string[];
}

export interface NumberRangeValue {
  min: number | null;
  max: number | null;
}

export interface RadioValue {
  status: string;
}

export interface NumberValue {
  value: number | null;
}

export interface TextValue {
  value: string;
}

export type FilterValue =
  | DateRangeValue
  | MultiSelectValue
  | NumberRangeValue
  | NumberValue
  | RadioValue
  | TextValue;

export interface ActiveFilter {
  attribute: FilterAttribute;
  value: FilterValue;
  isValid: boolean;
}

export interface FilterBuilderState {
  availableAttributes: FilterAttribute[];
  activeFilters: ActiveFilter[];
  isExecuting: boolean;
}

// Filter templates/presets
export interface FilterTemplate {
  id: string;
  name: string;
  description: string;
  filters: {
    attributeId: string;
    value: FilterValue;
  }[];
}

// Import the workflow filter attributes from the shared lib (used as default)
import { workflowFilterAttributes } from "@/lib/filterAttributes";

// Re-export as availableAttributes for backward compatibility
export const availableAttributes: FilterAttribute[] = workflowFilterAttributes;

// Filter templates
export const filterTemplates: FilterTemplate[] = [
  {
    id: "failed-calls-today",
    name: "Failed Calls Today",
    description: "Calls from today with failed disposition codes",
    filters: [
      {
        attributeId: "dateRange",
        value: {
          from: new Date(new Date().setHours(0, 0, 0, 0)),
          to: new Date(),
        } as DateRangeValue,
      },
      {
        attributeId: "dispositionCode",
        value: {
          codes: ["Failed", "No Answer", "Busy"],
        } as MultiSelectValue,
      },
    ],
  },
  {
    id: "long-duration-calls",
    name: "Long Duration Calls",
    description: "Completed calls longer than 5 minutes",
    filters: [
      {
        attributeId: "duration",
        value: {
          min: 300,
          max: 86400,
        } as NumberRangeValue,
      },
      {
        attributeId: "status",
        value: {
          status: "completed",
        } as RadioValue,
      },
    ],
  },
  {
    id: "high-cost-calls",
    name: "High Cost Calls",
    description: "Completed calls using more than 100 tokens",
    filters: [
      {
        attributeId: "tokenUsage",
        value: {
          min: 100,
          max: 10000,
        } as NumberRangeValue,
      },
      {
        attributeId: "status",
        value: {
          status: "completed",
        } as RadioValue,
      },
    ],
  },
  {
    id: "recent-activity",
    name: "Recent Activity",
    description: "All calls from the last 24 hours",
    filters: [
      {
        attributeId: "dateRange",
        value: {
          from: new Date(Date.now() - 24 * 60 * 60 * 1000),
          to: new Date(),
        } as DateRangeValue,
      },
    ],
  },
  {
    id: "transferred-calls",
    name: "Transferred Calls",
    description: "Calls with XFER disposition",
    filters: [
      {
        attributeId: "dispositionCode",
        value: {
          codes: ["XFER"],
        } as MultiSelectValue,
      },
    ],
  },
  {
    id: "active-calls",
    name: "Active Calls",
    description: "Calls currently in progress",
    filters: [
      {
        attributeId: "status",
        value: {
          status: "in_progress",
        } as RadioValue,
      },
    ],
  },
];
