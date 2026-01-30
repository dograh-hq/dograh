import { CalendarIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { getDatePresetValue } from "@/lib/filters";
import { cn } from "@/lib/utils";
import { DateRangeValue } from "@/types/filters";

interface DateRangeFilterProps {
  value: DateRangeValue;
  onChange: (value: DateRangeValue) => void;
  error?: string;
  presets?: string[];
}

export const DateRangeFilter: React.FC<DateRangeFilterProps> = ({
  value,
  onChange,
  error,
  presets = [],
}) => {
  const [isFromOpen, setIsFromOpen] = useState(false);
  const [isToOpen, setIsToOpen] = useState(false);

  // Local state for time inputs - only syncs to parent on blur
  const [fromTime, setFromTime] = useState(value.from?.toTimeString().slice(0, 5) ?? "");
  const [toTime, setToTime] = useState(value.to?.toTimeString().slice(0, 5) ?? "");

  // Sync local time state when parent value changes
  useEffect(() => {
    setFromTime(value.from?.toTimeString().slice(0, 5) ?? "");
  }, [value.from]);

  useEffect(() => {
    setToTime(value.to?.toTimeString().slice(0, 5) ?? "");
  }, [value.to]);

  const formatDate = (date: Date | null) => {
    if (!date) return "Select date";
    return date.toLocaleDateString() + " " + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const handlePresetClick = (preset: string) => {
    const presetValue = getDatePresetValue(preset);
    onChange(presetValue);
  };

  const handleFromChange = (date: Date | undefined) => {
    if (date) {
      // Keep the time from the existing date if available
      if (value.from) {
        date.setHours(value.from.getHours(), value.from.getMinutes());
      }
      onChange({ ...value, from: date });
    }
    setIsFromOpen(false);
  };

  const handleToChange = (date: Date | undefined) => {
    if (date) {
      // Set to end of day by default
      date.setHours(23, 59, 59, 999);
      onChange({ ...value, to: date });
    }
    setIsToOpen(false);
  };

  const handleTimeBlur = (type: 'from' | 'to') => {
    const timeString = type === 'from' ? fromTime : toTime;
    const [hours, minutes] = timeString.split(':').map(Number);
    const date = type === 'from' ? value.from : value.to;
    if (date && !isNaN(hours) && !isNaN(minutes)) {
      const newDate = new Date(date);
      newDate.setHours(hours, minutes);
      onChange({ ...value, [type]: newDate });
    }
  };

  return (
    <div className="space-y-3">
      {presets.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {presets.map((preset) => (
            <Button
              key={preset}
              variant="outline"
              size="sm"
              onClick={() => handlePresetClick(preset)}
            >
              {preset.charAt(0).toUpperCase() + preset.slice(1).replace(/(\d+)/, ' $1 ')}
            </Button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label>From</Label>
          <Popover open={isFromOpen} onOpenChange={setIsFromOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  "w-full justify-start text-left font-normal",
                  !value.from && "text-muted-foreground"
                )}
              >
                <CalendarIcon className="mr-2 h-4 w-4" />
                {formatDate(value.from)}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
              <Calendar
                mode="single"
                selected={value.from || undefined}
                onSelect={handleFromChange}
                initialFocus
              />
              {value.from && (
                <div className="p-3 border-t">
                  <Label htmlFor="from-time">Time</Label>
                  <input
                    id="from-time"
                    type="time"
                    className="w-full mt-1 px-3 py-2 border rounded-md"
                    value={fromTime}
                    onChange={(e) => setFromTime(e.target.value)}
                    onBlur={() => handleTimeBlur('from')}
                  />
                </div>
              )}
            </PopoverContent>
          </Popover>
        </div>

        <div className="space-y-2">
          <Label>To</Label>
          <Popover open={isToOpen} onOpenChange={setIsToOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  "w-full justify-start text-left font-normal",
                  !value.to && "text-muted-foreground"
                )}
              >
                <CalendarIcon className="mr-2 h-4 w-4" />
                {formatDate(value.to)}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
              <Calendar
                mode="single"
                selected={value.to || undefined}
                onSelect={handleToChange}
                initialFocus
                disabled={(date) => value.from ? date < value.from : false}
              />
              {value.to && (
                <div className="p-3 border-t">
                  <Label htmlFor="to-time">Time</Label>
                  <input
                    id="to-time"
                    type="time"
                    className="w-full mt-1 px-3 py-2 border rounded-md"
                    value={toTime}
                    onChange={(e) => setToTime(e.target.value)}
                    onBlur={() => handleTimeBlur('to')}
                  />
                </div>
              )}
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-500 mt-1">{error}</p>
      )}
    </div>
  );
};
