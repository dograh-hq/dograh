import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NumberRangeValue } from "@/types/filters";

interface NumberRangeFilterProps {
  value: NumberRangeValue;
  onChange: (value: NumberRangeValue) => void;
  error?: string;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  presets?: { label: string; min: number; max: number }[];
}

export const NumberRangeFilter: React.FC<NumberRangeFilterProps> = ({
  value,
  onChange,
  error,
  unit,
  min = 0,
  max = 999999,
  step = 1,
  presets = [],
}) => {
  // Local state for fast typing - only syncs to parent on blur
  const [localMin, setLocalMin] = useState<string>(value.min?.toString() ?? "");
  const [localMax, setLocalMax] = useState<string>(value.max?.toString() ?? "");

  // Sync local state when parent value changes (e.g., from URL, clear, or presets)
  useEffect(() => {
    setLocalMin(value.min?.toString() ?? "");
    setLocalMax(value.max?.toString() ?? "");
  }, [value.min, value.max]);

  const handleMinBlur = () => {
    const newValue = localMin === "" ? null : Number(localMin);
    onChange({ ...value, min: newValue });
  };

  const handleMaxBlur = () => {
    const newValue = localMax === "" ? null : Number(localMax);
    onChange({ ...value, max: newValue });
  };

  const handlePresetClick = (preset: { min: number; max: number }) => {
    onChange({ min: preset.min, max: preset.max });
  };

  return (
    <div className="space-y-3">
      {presets.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {presets.map((preset, index) => (
            <Button
              key={index}
              variant="outline"
              size="sm"
              onClick={() => handlePresetClick(preset)}
            >
              {preset.label}
            </Button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="min-value">
            Min {unit && `(${unit})`}
          </Label>
          <Input
            id="min-value"
            type="number"
            placeholder={`Min ${unit || 'value'}`}
            value={localMin}
            onChange={(e) => setLocalMin(e.target.value)}
            onBlur={handleMinBlur}
            min={min}
            max={max}
            step={step}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="max-value">
            Max {unit && `(${unit})`}
          </Label>
          <Input
            id="max-value"
            type="number"
            placeholder={`Max ${unit || 'value'}`}
            value={localMax}
            onChange={(e) => setLocalMax(e.target.value)}
            onBlur={handleMaxBlur}
            min={min}
            max={max}
            step={step}
          />
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-500 mt-1">{error}</p>
      )}
    </div>
  );
};
