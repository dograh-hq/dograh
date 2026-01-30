import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NumberValue } from "@/types/filters";

interface NumberFilterProps {
  value: NumberValue;
  onChange: (value: NumberValue) => void;
  error?: string;
  placeholder?: string;
  min?: number;
  max?: number;
  step?: number;
}

export const NumberFilter: React.FC<NumberFilterProps> = ({
  value,
  onChange,
  error,
  placeholder = "Enter value",
  min,
  max,
  step = 1,
}) => {
  // Local state for fast typing - only syncs to parent on blur
  const [localValue, setLocalValue] = useState<string>(value.value?.toString() ?? '');

  // Sync local state when parent value changes (e.g., from URL or clear)
  useEffect(() => {
    setLocalValue(value.value?.toString() ?? '');
  }, [value.value]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalValue(e.target.value);
  };

  const handleBlur = () => {
    if (localValue === '') {
      onChange({ value: null });
    } else {
      const num = parseInt(localValue, 10);
      if (!isNaN(num)) {
        onChange({ value: num });
      }
    }
  };

  return (
    <div className="space-y-2">
      <div className="space-y-2">
        <Label htmlFor="number-filter">Value</Label>
        <Input
          id="number-filter"
          type="number"
          value={localValue}
          onChange={handleChange}
          onBlur={handleBlur}
          placeholder={placeholder}
          min={min}
          max={max}
          step={step}
          className={error ? "border-red-500" : ""}
        />
      </div>
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
};
