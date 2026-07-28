import { formatLocalDateTime } from "@/lib/dateTime";
import { ActiveFilter, DateRangeValue, FilterAttribute, FilterValue, MultiSelectValue, NumberRangeValue, NumberValue, RadioValue, TextValue } from "@/types/filters";

// Get default value based on attribute type
export const getDefaultValue = (type: FilterAttribute["type"]): FilterValue => {
  switch (type) {
    case "dateRange":
      return { from: null, to: null };
    case "multiSelect":
      return { codes: [] };
    case "number":
    case "numberSelect":
      return { value: null };
    case "numberRange":
      return { min: null, max: null };
    case "radio":
      return { status: "all" };
    case "tags":
      return { codes: [] };
    case "text":
      return { value: "" };
    default:
      throw new Error(`Unknown filter type: ${type}`);
  }
};

// Validate filter based on attribute type
export const validateFilter = (filter: ActiveFilter): string | null => {
  switch (filter.attribute.type) {
    case "dateRange": {
      const value = filter.value as DateRangeValue;
      if (!value.from || !value.to) {
        return "Both dates are required";
      }
      if (value.to < value.from) {
        return "End date must be after start date";
      }

      // Check max range if configured
      const config = filter.attribute.config;
      if (config.maxRangeDays) {
        const daysDiff = Math.ceil((value.to.getTime() - value.from.getTime()) / (1000 * 60 * 60 * 24));
        if (daysDiff > config.maxRangeDays) {
          return `Date range cannot exceed ${config.maxRangeDays} days`;
        }
      }
      break;
    }
    case "multiSelect": {
      const value = filter.value as MultiSelectValue;
      if (!value.codes.length) {
        return "At least one option must be selected";
      }

      const config = filter.attribute.config;
      if (config.maxSelections && value.codes.length > config.maxSelections) {
        return `Cannot select more than ${config.maxSelections} options`;
      }
      break;
    }
    case "numberRange": {
      const value = filter.value as NumberRangeValue;
      if (value.min === null || value.max === null) {
        return "Both values are required";
      }
      if (value.min > value.max) {
        return "Minimum must be less than maximum";
      }

      const config = filter.attribute.config;
      if (config.min !== undefined && value.min < config.min) {
        return `Minimum value cannot be less than ${config.min}`;
      }
      if (config.max !== undefined && value.max > config.max) {
        return `Maximum value cannot be greater than ${config.max}`;
      }
      break;
    }
    case "number": {
      const value = filter.value as NumberValue;
      if (value.value === null) {
        return "A value is required";
      }
      const config = filter.attribute.config;
      if (config.min !== undefined && value.value < config.min) {
        return `Value cannot be less than ${config.min}`;
      }
      if (config.max !== undefined && value.value > config.max) {
        return `Value cannot be greater than ${config.max}`;
      }
      break;
    }
    case "numberSelect": {
      const value = filter.value as NumberValue;
      if (value.value === null) {
        return "A value is required";
      }
      break;
    }
    case "radio": {
      const value = filter.value as RadioValue;
      if (!value.status) {
        return "A status must be selected";
      }
      break;
    }
    case "tags": {
      const value = filter.value as MultiSelectValue;
      if (!value.codes.length) {
        return "At least one tag must be entered";
      }
      break;
    }
    case "text": {
      const value = filter.value as TextValue;
      if (!value.value || value.value.trim() === "") {
        return "Text value is required";
      }
      break;
    }
  }
  return null;
};

// Encode filters to URL parameters
export const encodeFiltersToURL = (filters: ActiveFilter[]): string => {
  const params = new URLSearchParams();

  if (filters.length > 0) {
    const filterData = filters.map(filter => ({
      id: filter.attribute.id,
      value: filter.value
    }));
    params.set("filters", JSON.stringify(filterData));
  }

  return params.toString();
};

// Decode filters from URL parameters
export const decodeFiltersFromURL = (
  params: URLSearchParams,
  availableAttributes: FilterAttribute[]
): ActiveFilter[] => {
  const filtersParam = params.get("filters");
  if (!filtersParam) return [];

  try {
    const filterData = JSON.parse(filtersParam) as Array<{
      id: string;
      value: FilterValue;
    }>;

    return filterData.map(item => {
      const attribute = availableAttributes.find(attr => attr.id === item.id);
      if (!attribute) {
        throw new Error(`Unknown filter attribute: ${item.id}`);
      }

      // Convert date strings back to Date objects for dateRange filters
      let value = item.value;
      if (attribute.type === "dateRange" && value) {
        const dateValue = value as { from: string | null; to: string | null };
        value = {
          from: dateValue.from ? new Date(dateValue.from) : null,
          to: dateValue.to ? new Date(dateValue.to) : null,
        };
      }

      const filter: ActiveFilter = {
        attribute,
        value,
        isValid: false
      };

      // Validate the filter
      filter.isValid = validateFilter(filter) === null;

      return filter;
    });
  } catch (error) {
    console.error("Failed to decode filters from URL:", error);
    return [];
  }
};

// Format date range for display
export const formatDateRange = (value: DateRangeValue): string => {
  if (!value.from || !value.to) return "No date range selected";

  return `${formatLocalDateTime(value.from)} to ${formatLocalDateTime(value.to)}`;
};

// Format number range for display
export const formatNumberRange = (value: NumberRangeValue, unit?: string): string => {
  if (value.min === null || value.max === null) return "No range selected";

  const unitSuffix = unit ? ` ${unit}` : "";
  return `${value.min}${unitSuffix} - ${value.max}${unitSuffix}`;
};

// Get date preset value
export const getDatePresetValue = (preset: string): DateRangeValue => {
  const now = new Date();
  // Start of today (00:00:00.000)
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
  // End of today (23:59:59.999)
  const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);

  switch (preset) {
    case "today":
      return { from: todayStart, to: todayEnd };
    case "yesterday": {
      const yesterdayStart = new Date(todayStart);
      yesterdayStart.setDate(yesterdayStart.getDate() - 1);
      const yesterdayEnd = new Date(todayStart);
      yesterdayEnd.setMilliseconds(-1); // One millisecond before today start
      return { from: yesterdayStart, to: yesterdayEnd };
    }
    case "last7days": {
      const last7DaysStart = new Date(todayStart);
      last7DaysStart.setDate(last7DaysStart.getDate() - 6); // -6 because today is included
      return { from: last7DaysStart, to: todayEnd };
    }
    case "last30days": {
      const last30DaysStart = new Date(todayStart);
      last30DaysStart.setDate(last30DaysStart.getDate() - 29); // -29 because today is included
      return { from: last30DaysStart, to: todayEnd };
    }
    default:
      return { from: null, to: null };
  }
};
