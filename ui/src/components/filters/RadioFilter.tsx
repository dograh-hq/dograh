import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { RadioValue } from "@/types/filters";

interface RadioFilterProps {
  value: RadioValue;
  onChange: (value: RadioValue) => void;
  error?: string;
  options: { label: string; value: string }[];
  // Heading above the options. Defaults to the completion-status wording this
  // filter originally served.
  label?: string;
}

export const RadioFilter: React.FC<RadioFilterProps> = ({
  value,
  onChange,
  error,
  options,
  label = "Select Status",
}) => {
  const handleChange = (newValue: string) => {
    onChange({ status: newValue });
  };

  return (
    <div className="space-y-3">
      <Label>{label}</Label>
      <RadioGroup value={value.status} onValueChange={handleChange}>
        {options.map((option) => (
          <div key={option.value} className="flex items-center space-x-2">
            <RadioGroupItem value={option.value} id={option.value} />
            <Label htmlFor={option.value} className="font-normal cursor-pointer">
              {option.label}
            </Label>
          </div>
        ))}
      </RadioGroup>

      {error && (
        <p className="text-sm text-red-500 mt-1">{error}</p>
      )}
    </div>
  );
};
