import { ChangeEvent, useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MultiSelectValue } from "@/types/filters";

interface TagInputFilterProps {
  value: MultiSelectValue;
  onChange: (value: MultiSelectValue) => void;
  error?: string;
  placeholder?: string;
}

export const TagInputFilter: React.FC<TagInputFilterProps> = ({ value, onChange, error, placeholder="Enter tags (comma separated)" }) => {
  const [text, setText] = useState(value.codes.join(", "));

  // Sync local state when parent value changes (e.g., from URL or clear)
  useEffect(() => {
    setText(value.codes.join(", "));
  }, [value.codes]);

  const handleBlur = (e: ChangeEvent<HTMLInputElement>) => {
    const tags = e.target.value
      .split(/[,\n]/)
      .map((tag) => tag.trim())
      .filter((tag) => tag.length > 0);
    onChange({ codes: Array.from(new Set(tags)) });
  };

  return (
    <div className="space-y-2">
      <Label>Tags</Label>
      <Input
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onBlur={handleBlur}
      />
      {error && <p className="text-sm text-red-500 mt-1">{error}</p>}
    </div>
  );
};
