import {
    type ChangeEvent,
    type KeyboardEvent,
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";

import type { RecordingResponseSchema } from "@/client/types.gen";
import { cn } from "@/lib/utils";

export interface MentionItem {
    id: string;
    name: string;
    description: string;
    filename: string;
}

interface MentionTextareaProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    className?: string;
    recordings?: RecordingResponseSchema[];
}

export function MentionTextarea({
    value,
    onChange,
    placeholder,
    className,
    recordings = [],
}: MentionTextareaProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);
    const [showDropdown, setShowDropdown] = useState(false);
    const [query, setQuery] = useState("");
    const [mentionStartIndex, setMentionStartIndex] = useState<number | null>(null);
    const [selectedIndex, setSelectedIndex] = useState(0);

    // Convert recordings to mention items
    const items: MentionItem[] = useMemo(
        () =>
            recordings.map((r) => ({
                id: r.recording_id,
                name: r.transcript,
                description: r.transcript,
                filename: (r.metadata?.original_filename as string) || r.recording_id,
            })),
        [recordings]
    );

    const filtered = items.filter(
        (item) =>
            item.name.toLowerCase().includes(query.toLowerCase()) ||
            item.id.toLowerCase().includes(query.toLowerCase())
    );

    const insertMention = useCallback(
        (item: MentionItem) => {
            if (mentionStartIndex === null) return;
            const textarea = textareaRef.current;
            if (!textarea) return;

            const before = value.slice(0, mentionStartIndex);
            const after = value.slice(textarea.selectionStart);
            const mentionText = `RECORDING_ID: ${item.id} [ ${item.description} ]`;
            const newValue = before + mentionText + after;

            onChange(newValue);
            setShowDropdown(false);
            setQuery("");
            setMentionStartIndex(null);
            setSelectedIndex(0);

            // Restore cursor position after the inserted mention
            requestAnimationFrame(() => {
                const cursorPos = before.length + mentionText.length;
                textarea.focus();
                textarea.setSelectionRange(cursorPos, cursorPos);
            });
        },
        [mentionStartIndex, value, onChange]
    );

    const handleChange = useCallback(
        (e: ChangeEvent<HTMLTextAreaElement>) => {
            const newValue = e.target.value;
            const cursorPos = e.target.selectionStart;
            onChange(newValue);

            // Look backwards from cursor to find an unmatched "@"
            const textBeforeCursor = newValue.slice(0, cursorPos);
            const lastAtIndex = textBeforeCursor.lastIndexOf("@");

            if (lastAtIndex !== -1) {
                const textBetween = textBeforeCursor.slice(lastAtIndex + 1);
                // Only trigger if there's no space before the query or it's at the start
                const charBeforeAt = lastAtIndex > 0 ? newValue[lastAtIndex - 1] : " ";
                if (
                    (charBeforeAt === " " || charBeforeAt === "\n" || lastAtIndex === 0) &&
                    !textBetween.includes(" ")
                ) {
                    setShowDropdown(true);
                    setQuery(textBetween);
                    setMentionStartIndex(lastAtIndex);
                    setSelectedIndex(0);
                    return;
                }
            }

            setShowDropdown(false);
            setQuery("");
            setMentionStartIndex(null);
        },
        [onChange]
    );

    const handleKeyDown = useCallback(
        (e: KeyboardEvent<HTMLTextAreaElement>) => {
            if (!showDropdown || filtered.length === 0) return;

            if (e.key === "ArrowDown") {
                e.preventDefault();
                setSelectedIndex((prev) => (prev + 1) % filtered.length);
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setSelectedIndex((prev) => (prev - 1 + filtered.length) % filtered.length);
            } else if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                insertMention(filtered[selectedIndex]);
            } else if (e.key === "Escape") {
                e.preventDefault();
                setShowDropdown(false);
            }
        },
        [showDropdown, filtered, selectedIndex, insertMention]
    );

    // Close dropdown when clicking outside
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (
                dropdownRef.current &&
                !dropdownRef.current.contains(e.target as Node) &&
                textareaRef.current &&
                !textareaRef.current.contains(e.target as Node)
            ) {
                setShowDropdown(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    // Scroll selected item into view
    useEffect(() => {
        if (!showDropdown || !dropdownRef.current) return;
        const selected = dropdownRef.current.querySelector("[data-selected='true']");
        selected?.scrollIntoView({ block: "nearest" });
    }, [selectedIndex, showDropdown]);

    return (
        <div className="relative">
            <textarea
                ref={textareaRef}
                value={value}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                className={cn(
                    "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:bg-input/30 flex field-sizing-content min-h-16 w-full rounded-md border bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
                    className
                )}
            />
            {showDropdown && filtered.length > 0 && (
                <div
                    ref={dropdownRef}
                    className="absolute z-50 mt-1 w-full max-h-60 overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-md"
                >
                    {filtered.map((item, index) => (
                        <button
                            key={item.id}
                            type="button"
                            data-selected={index === selectedIndex}
                            className={cn(
                                "flex w-full flex-col gap-0.5 px-3 py-2 text-left text-sm cursor-pointer hover:bg-accent",
                                index === selectedIndex && "bg-accent"
                            )}
                            onMouseDown={(e) => {
                                e.preventDefault(); // prevent textarea blur
                                insertMention(item);
                            }}
                            onMouseEnter={() => setSelectedIndex(index)}
                        >
                            <div className="flex items-center gap-2">
                                <code className="text-xs bg-muted px-1 py-0.5 rounded font-mono">
                                    {item.filename}
                                </code>
                                <span className="font-medium truncate">{item.name}</span>
                            </div>
                        </button>
                    ))}
                </div>
            )}
            {showDropdown && filtered.length === 0 && items.length === 0 && (
                <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover text-popover-foreground shadow-md p-3 text-sm text-muted-foreground">
                    No recordings found. Upload recordings via the Recordings panel.
                </div>
            )}
        </div>
    );
}
