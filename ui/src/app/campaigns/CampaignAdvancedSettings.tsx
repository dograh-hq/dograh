"use client";

import { Plus, X } from 'lucide-react';
import { useId } from 'react';
import TimezoneSelect, { type ITimezoneOption } from 'react-timezone-select';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';

export type TimeSlot = { day_of_week: number; start_time: string; end_time: string };

export interface CampaignAdvancedSettingsProps {
    // Concurrency
    maxConcurrency: string;
    onMaxConcurrencyChange: (value: string) => void;
    effectiveLimit: number;
    orgConcurrentLimit: number;
    fromNumbersCount: number;
    // Retry config
    retryEnabled: boolean;
    onRetryEnabledChange: (value: boolean) => void;
    maxRetries: string;
    onMaxRetriesChange: (value: string) => void;
    retryDelaySeconds: string;
    onRetryDelaySecondsChange: (value: string) => void;
    retryOnBusy: boolean;
    onRetryOnBusyChange: (value: boolean) => void;
    retryOnNoAnswer: boolean;
    onRetryOnNoAnswerChange: (value: boolean) => void;
    retryOnVoicemail: boolean;
    onRetryOnVoicemailChange: (value: boolean) => void;
    // Schedule config
    scheduleEnabled: boolean;
    onScheduleEnabledChange: (value: boolean) => void;
    scheduleTimezone: ITimezoneOption | string;
    onScheduleTimezoneChange: (value: ITimezoneOption | string) => void;
    timeSlots: TimeSlot[];
    onTimeSlotsChange: (value: TimeSlot[]) => void;
}

/** Extract the string timezone value from ITimezoneOption | string */
export function getTimezoneValue(tz: ITimezoneOption | string): string {
    return typeof tz === 'string' ? tz : tz.value;
}

const timezoneSelectStyles = {
    control: (base: Record<string, unknown>, state: { isFocused: boolean }) => ({
        ...base,
        minHeight: '36px',
        fontSize: '14px',
        backgroundColor: 'var(--background)',
        borderColor: state.isFocused ? 'var(--ring)' : 'var(--border)',
        boxShadow: state.isFocused ? '0 0 0 2px color-mix(in srgb, var(--ring) 20%, transparent)' : 'none',
        '&:hover': { borderColor: 'var(--border)' },
    }),
    menu: (base: Record<string, unknown>) => ({
        ...base,
        zIndex: 9999,
        backgroundColor: 'var(--popover)',
        border: '1px solid var(--border)',
        boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
    }),
    menuList: (base: Record<string, unknown>) => ({
        ...base,
        backgroundColor: 'var(--popover)',
        padding: 0,
    }),
    option: (base: Record<string, unknown>, state: { isSelected: boolean; isFocused: boolean }) => ({
        ...base,
        backgroundColor: state.isSelected ? 'var(--accent)' : state.isFocused ? 'var(--accent)' : 'var(--popover)',
        color: 'var(--foreground)',
        cursor: 'pointer',
        '&:active': { backgroundColor: 'var(--accent)' },
    }),
    singleValue: (base: Record<string, unknown>) => ({ ...base, color: 'var(--foreground)' }),
    input: (base: Record<string, unknown>) => ({ ...base, color: 'var(--foreground)' }),
    placeholder: (base: Record<string, unknown>) => ({ ...base, color: 'var(--muted-foreground)' }),
    indicatorSeparator: (base: Record<string, unknown>) => ({ ...base, backgroundColor: 'var(--border)' }),
    dropdownIndicator: (base: Record<string, unknown>) => ({
        ...base,
        color: 'var(--muted-foreground)',
        '&:hover': { color: 'var(--foreground)' },
    }),
};

export default function CampaignAdvancedSettings({
    maxConcurrency, onMaxConcurrencyChange, effectiveLimit, orgConcurrentLimit, fromNumbersCount,
    retryEnabled, onRetryEnabledChange, maxRetries, onMaxRetriesChange,
    retryDelaySeconds, onRetryDelaySecondsChange,
    retryOnBusy, onRetryOnBusyChange, retryOnNoAnswer, onRetryOnNoAnswerChange,
    retryOnVoicemail, onRetryOnVoicemailChange,
    scheduleEnabled, onScheduleEnabledChange, scheduleTimezone, onScheduleTimezoneChange,
    timeSlots, onTimeSlotsChange,
}: CampaignAdvancedSettingsProps) {
    const timezoneSelectId = useId();

    return (
        <div className="space-y-6">
            {/* Max Concurrent Calls */}
            <div className="space-y-2">
                <Label htmlFor="max-concurrency">Max Concurrent Calls</Label>
                <Input
                    id="max-concurrency"
                    type="number"
                    placeholder={`Default: ${effectiveLimit}`}
                    value={maxConcurrency}
                    onChange={(e) => onMaxConcurrencyChange(e.target.value)}
                    min={1}
                    max={effectiveLimit}
                />
                <p className="text-sm text-muted-foreground">
                    Maximum number of simultaneous calls. Leave empty to use {effectiveLimit}.
                    {fromNumbersCount > 0 && ` You have ${fromNumbersCount} CLI${fromNumbersCount !== 1 ? 's' : ''} and an org limit of ${orgConcurrentLimit}.`}
                </p>
                {fromNumbersCount > 0 && fromNumbersCount < orgConcurrentLimit && (
                    <p className="text-sm text-amber-600 dark:text-amber-400">
                        Concurrency is limited to {fromNumbersCount} by your configured phone numbers. To use the full org limit of {orgConcurrentLimit}, add more CLIs in <a href="/telephony-configurations" className="underline font-medium">Telephony Configuration</a>.
                    </p>
                )}
                {fromNumbersCount === 0 && (
                    <p className="text-sm text-amber-600 dark:text-amber-400">
                        No phone numbers configured. Add CLIs in <a href="/telephony-configurations" className="underline font-medium">Telephony Configuration</a> before running the campaign.
                    </p>
                )}
            </div>

            {/* Retry Configuration */}
            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <div>
                        <Label htmlFor="retry-enabled">Enable Retries</Label>
                        <p className="text-sm text-muted-foreground">
                            Automatically retry failed calls
                        </p>
                    </div>
                    <Switch
                        id="retry-enabled"
                        checked={retryEnabled}
                        onCheckedChange={onRetryEnabledChange}
                    />
                </div>

                {retryEnabled && (
                    <div className="space-y-4 pl-4 border-l-2 border-muted">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="max-retries">Max Retries</Label>
                                <Input
                                    id="max-retries"
                                    type="number"
                                    value={maxRetries}
                                    onChange={(e) => onMaxRetriesChange(e.target.value)}
                                    min={0}
                                    max={10}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="retry-delay">Retry Delay (seconds)</Label>
                                <Input
                                    id="retry-delay"
                                    type="number"
                                    value={retryDelaySeconds}
                                    onChange={(e) => onRetryDelaySecondsChange(e.target.value)}
                                    min={30}
                                    max={3600}
                                />
                            </div>
                        </div>

                        <div className="space-y-3">
                            <Label>Retry On</Label>
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <span className="text-sm">Busy Signal</span>
                                    <Switch checked={retryOnBusy} onCheckedChange={onRetryOnBusyChange} />
                                </div>
                                <div className="flex items-center justify-between">
                                    <span className="text-sm">No Answer</span>
                                    <Switch checked={retryOnNoAnswer} onCheckedChange={onRetryOnNoAnswerChange} />
                                </div>
                                <div className="flex items-center justify-between">
                                    <span className="text-sm">Voicemail</span>
                                    <Switch checked={retryOnVoicemail} onCheckedChange={onRetryOnVoicemailChange} />
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            <Separator />

            {/* Call Schedule */}
            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <div>
                        <Label htmlFor="schedule-enabled">Call Schedule</Label>
                        <p className="text-sm text-muted-foreground">
                            Restrict when calls are made
                        </p>
                    </div>
                    <Switch
                        id="schedule-enabled"
                        checked={scheduleEnabled}
                        onCheckedChange={onScheduleEnabledChange}
                    />
                </div>

                {scheduleEnabled && (
                    <div className="space-y-4 pl-4 border-l-2 border-muted">
                        <div className="space-y-2">
                            <Label>Timezone</Label>
                            <TimezoneSelect
                                instanceId={timezoneSelectId}
                                value={scheduleTimezone}
                                onChange={onScheduleTimezoneChange}
                                styles={timezoneSelectStyles}
                            />
                        </div>

                        <div className="space-y-3">
                            <Label>Time Slots</Label>
                            {timeSlots.map((slot, index) => (
                                <div key={index} className="flex items-center gap-2">
                                    <Select
                                        value={String(slot.day_of_week)}
                                        onValueChange={(val) => {
                                            const updated = [...timeSlots];
                                            updated[index] = { ...updated[index], day_of_week: parseInt(val) };
                                            onTimeSlotsChange(updated);
                                        }}
                                    >
                                        <SelectTrigger className="w-[120px]">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day, i) => (
                                                <SelectItem key={i} value={String(i)}>{day}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <Input
                                        type="time"
                                        value={slot.start_time}
                                        onChange={(e) => {
                                            const updated = [...timeSlots];
                                            updated[index] = { ...updated[index], start_time: e.target.value };
                                            onTimeSlotsChange(updated);
                                        }}
                                        className="w-[130px]"
                                    />
                                    <span className="text-sm text-muted-foreground">to</span>
                                    <Input
                                        type="time"
                                        value={slot.end_time}
                                        onChange={(e) => {
                                            const updated = [...timeSlots];
                                            updated[index] = { ...updated[index], end_time: e.target.value };
                                            onTimeSlotsChange(updated);
                                        }}
                                        className="w-[130px]"
                                    />
                                    {timeSlots.length > 1 && (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            onClick={() => onTimeSlotsChange(timeSlots.filter((_, i) => i !== index))}
                                        >
                                            <X className="h-4 w-4" />
                                        </Button>
                                    )}
                                </div>
                            ))}
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => onTimeSlotsChange([...timeSlots, { day_of_week: 0, start_time: '09:00', end_time: '17:00' }])}
                            >
                                <Plus className="h-4 w-4 mr-1" />
                                Add Time Slot
                            </Button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
