import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { TextValue } from "@/types/filters";

interface TextFilterProps {
  value: TextValue;
  onChange: (value: TextValue) => void;
  error?: string;
  placeholder?: string;
  maxLength?: number;
}

export const TextFilter: React.FC<TextFilterProps> = ({
  value,
  onChange,
  error,
  placeholder = "Enter text",
  maxLength,
}) => {
  // Local state for fast typing - only syncs to parent on blur
  const [localValue, setLocalValue] = useState(value.value || "");

  // Sync local state when parent value changes (e.g., from URL or clear)
  useEffect(() => {
    setLocalValue(value.value || "");
  }, [value.value]);

  const handleBlur = () => {
    onChange({ value: localValue });
  };

  return (
    <div className="space-y-2">
      <Input
        type="text"
        value={localValue}
        onChange={(e) => setLocalValue(e.target.value)}
        onBlur={handleBlur}
        placeholder={placeholder}
        maxLength={maxLength}
        className={error ? "border-red-500" : ""}
      />
      {error && <p className="text-sm text-red-500">{error}</p>}
    </div>
  );
};
