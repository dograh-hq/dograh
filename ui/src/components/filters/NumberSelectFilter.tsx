import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { NumberFilterOption, NumberValue } from "@/types/filters";

interface NumberSelectFilterProps {
  value: NumberValue;
  onChange: (value: NumberValue) => void;
  error?: string;
  label?: string;
  placeholder?: string;
  options: NumberFilterOption[];
  isLoading?: boolean;
}

export const NumberSelectFilter: React.FC<NumberSelectFilterProps> = ({
  value,
  onChange,
  error,
  label = "Option",
  placeholder = "Select an option",
  options,
  isLoading = false,
}) => {
  const selectedOptionExists = options.some(option => option.value === value.value);
  const unavailableSelection =
    value.value !== null && !selectedOptionExists
      ? { label: `Unavailable agent (#${value.value})`, value: value.value }
      : null;

  return (
    <div className="space-y-2">
      <div className="space-y-2">
        <Label>{label}</Label>
        <Select
          value={value.value === null ? "" : value.value.toString()}
          onValueChange={(selectedValue) => {
            const numericValue = parseInt(selectedValue, 10);
            onChange({ value: Number.isNaN(numericValue) ? null : numericValue });
          }}
          disabled={isLoading || options.length === 0}
        >
          <SelectTrigger className={error ? "border-red-500" : ""}>
            <SelectValue placeholder={isLoading ? "Loading options..." : placeholder} />
          </SelectTrigger>
          <SelectContent>
            {unavailableSelection && (
              <SelectItem value={unavailableSelection.value.toString()} disabled>
                {unavailableSelection.label}
              </SelectItem>
            )}
            {options.length === 0 ? (
              <div className="px-2 py-1.5 text-sm text-muted-foreground">
                No options found
              </div>
            ) : (
              options.map((option) => (
                <SelectItem key={option.value} value={option.value.toString()}>
                  {option.label}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
      </div>
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
};
