import { Code2, GitBranch, Mic, Repeat, Sparkles, Split } from "lucide-react";
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
import { getCaretCoordinates } from "@/lib/caretPosition";
import { cn } from "@/lib/utils";

export interface MentionItem {
    id: string;
    name: string;
    description: string;
    filename: string;
}

export interface JinjaSnippetItem {
    id: string;
    title: string;
    badge: string;
    category: "condition" | "loop" | "variable" | "custom";
    description: string;
    snippet: string;
    cursorOffset: number;
    selectionLength: number;
}

interface JinjaToken {
    type: "text" | "block" | "var";
    content: string;
}

/**
 * Tokenizes text into plain text and Jinja syntax tokens ({% ... %} or {{ ... }}).
 * Used ONLY for highlighting specific Jinja tags without shading surrounding plain text.
 */
function tokenizeJinjaTags(text: string): JinjaToken[] {
    if (!text) return [];
    return text
        .split(/(\{%.*?%\}|\{\{.*?\}\})/s)
        .filter(Boolean)
        .map((part) => ({
            type: part.startsWith("{%") ? "block" : part.startsWith("{{") ? "var" : "text",
            content: part,
        }));
}

export const JINJA_SNIPPETS: JinjaSnippetItem[] = [
    {
        id: "if",
        title: "{% if condition %}",
        badge: "Condition",
        category: "condition",
        description: "Branch prompt instructions based on a variable",
        snippet: "{% if condition %}\n  \n{% endif %}",
        cursorOffset: 6,
        selectionLength: 9, // highlights 'condition'
    },
    {
        id: "if_else",
        title: "{% if ... else %}",
        badge: "Branch",
        category: "condition",
        description: "Two-way condition with fallback instructions",
        snippet: "{% if condition %}\n  \n{% else %}\n  \n{% endif %}",
        cursorOffset: 6,
        selectionLength: 9, // highlights 'condition'
    },
    {
        id: "if_elif",
        title: "{% if ... elif ... else %}",
        badge: "Multi-branch",
        category: "condition",
        description: "Multi-way conditional decision tree",
        snippet: "{% if condition %}\n  \n{% elif other_condition %}\n  \n{% else %}\n  \n{% endif %}",
        cursorOffset: 6,
        selectionLength: 9, // highlights 'condition'
    },
    {
        id: "for",
        title: "{% for item in items %}",
        badge: "Loop",
        category: "loop",
        description: "Iterate over a list of items, products, or options",
        snippet: "{% for item in items %}\n  - {{ item }}\n{% endfor %}",
        cursorOffset: 15,
        selectionLength: 5, // highlights 'items'
    },
    {
        id: "set",
        title: "{% set var = value %}",
        badge: "Variable",
        category: "variable",
        description: "Assign or define a local template variable",
        snippet: '{% set variable_name = "value" %}',
        cursorOffset: 7,
        selectionLength: 13, // highlights 'variable_name'
    },
    {
        id: "custom",
        title: "{% ... %}",
        badge: "Custom",
        category: "custom",
        description: "Write your own logic — full Jinja2 expressions, filters & tags are supported",
        snippet: "{%  %}",
        cursorOffset: 3,
        selectionLength: 0,
    },
];

interface MentionTextareaProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    className?: string;
    recordings?: RecordingResponseSchema[];
}

interface DropdownPosition {
    top: number;
    left: number;
    placement: "bottom" | "top";
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
    const backdropRef = useRef<HTMLDivElement>(null);

    const tokens = useMemo(() => tokenizeJinjaTags(value), [value]);
    const hasJinja = useMemo(() => tokens.some((t) => t.type !== "text"), [tokens]);

    const [showDropdown, setShowDropdown] = useState(false);
    const [triggerType, setTriggerType] = useState<"recording" | "jinja" | null>(null);
    const [query, setQuery] = useState("");
    const [mentionStartIndex, setMentionStartIndex] = useState<number | null>(null);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [dropdownPos, setDropdownPos] = useState<DropdownPosition>({
        top: 0,
        left: 0,
        placement: "bottom",
    });

    // Convert recordings to mention items
    const recordingItems: MentionItem[] = useMemo(
        () =>
            recordings.map((r) => ({
                id: r.recording_id,
                name: r.transcript,
                description: r.transcript,
                filename: (r.metadata?.original_filename as string) || r.recording_id,
            })),
        [recordings]
    );

    const filteredRecordings = useMemo(
        () =>
            recordingItems.filter(
                (item) =>
                    item.name.toLowerCase().includes(query.toLowerCase()) ||
                    item.id.toLowerCase().includes(query.toLowerCase())
            ),
        [recordingItems, query]
    );

    const filteredJinjaSnippets = useMemo(
        () =>
            JINJA_SNIPPETS.filter(
                (item) =>
                    item.title.toLowerCase().includes(query.toLowerCase()) ||
                    item.id.toLowerCase().includes(query.toLowerCase()) ||
                    item.badge.toLowerCase().includes(query.toLowerCase()) ||
                    item.description.toLowerCase().includes(query.toLowerCase())
            ),
        [query]
    );

    const activeListLength =
        triggerType === "jinja" ? filteredJinjaSnippets.length : filteredRecordings.length;

    // Recalculate dropdown position at the caret
    const updateDropdownPosition = useCallback((startIndex: number) => {
        const textarea = textareaRef.current;
        if (!textarea) return;

        const coords = getCaretCoordinates(textarea, startIndex);
        const textareaWidth = textarea.clientWidth;
        const textareaHeight = textarea.clientHeight;
        const padding = 8;
        const popoverWidth = Math.min(420, Math.max(260, textareaWidth - padding * 2));
        const popoverHeight = 310;

        // Clamp horizontal position so the popover never clips outside textarea width
        const left = Math.max(
            padding,
            Math.min(coords.left, textareaWidth - popoverWidth - padding)
        );

        // Calculate vertical placement (flip to top if cramped below)
        const spaceBelow = textareaHeight - (coords.top + coords.lineHeight);
        const shouldPlaceAbove = spaceBelow < 200 && coords.top > 200;

        let top: number;
        let placement: "bottom" | "top";
        if (shouldPlaceAbove) {
            top = Math.max(padding, coords.top - popoverHeight - 6);
            placement = "top";
        } else {
            top = coords.top + coords.lineHeight + 6;
            placement = "bottom";
        }

        setDropdownPos({ top, left, placement });
    }, []);

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
            setTriggerType(null);
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

    const insertJinjaSnippet = useCallback(
        (item: JinjaSnippetItem) => {
            if (mentionStartIndex === null) return;
            const textarea = textareaRef.current;
            if (!textarea) return;

            const before = value.slice(0, mentionStartIndex);
            const after = value.slice(textarea.selectionStart);
            const cleanAfter = after.startsWith("%}") ? after.slice(2) : after;
            const newValue = before + item.snippet + cleanAfter;

            onChange(newValue);
            setShowDropdown(false);
            setTriggerType(null);
            setQuery("");
            setMentionStartIndex(null);
            setSelectedIndex(0);

            // Highlight the placeholder word (e.g. 'condition' or 'items')
            requestAnimationFrame(() => {
                const start = before.length + item.cursorOffset;
                const end = start + item.selectionLength;
                textarea.focus();
                textarea.setSelectionRange(start, end);
            });
        },
        [mentionStartIndex, value, onChange]
    );

    const handleChange = useCallback(
        (e: ChangeEvent<HTMLTextAreaElement>) => {
            const newValue = e.target.value;
            const cursorPos = e.target.selectionStart;
            onChange(newValue);

            const textBeforeCursor = newValue.slice(0, cursorPos);
            const lastJinjaIndex = textBeforeCursor.lastIndexOf("{%");
            const lastAtIndex = textBeforeCursor.lastIndexOf("@");

            // Check if {% was typed
            if (lastJinjaIndex !== -1 && (lastAtIndex === -1 || lastJinjaIndex > lastAtIndex)) {
                const textBetween = textBeforeCursor.slice(lastJinjaIndex + 2);
                if (!textBetween.includes("%}") && !textBetween.includes("\n")) {
                    setTriggerType("jinja");
                    setShowDropdown(true);
                    setQuery(textBetween.trim());
                    setMentionStartIndex(lastJinjaIndex);
                    setSelectedIndex(0);
                    updateDropdownPosition(lastJinjaIndex);
                    return;
                }
            }

            // Check for @
            if (lastAtIndex !== -1) {
                const textBetween = textBeforeCursor.slice(lastAtIndex + 1);
                const charBeforeAt = lastAtIndex > 0 ? newValue[lastAtIndex - 1] : " ";
                if (
                    (charBeforeAt === " " || charBeforeAt === "\n" || lastAtIndex === 0) &&
                    !textBetween.includes(" ")
                ) {
                    setTriggerType("recording");
                    setShowDropdown(true);
                    setQuery(textBetween);
                    setMentionStartIndex(lastAtIndex);
                    setSelectedIndex(0);
                    updateDropdownPosition(lastAtIndex);
                    return;
                }
            }

            setShowDropdown(false);
            setTriggerType(null);
            setQuery("");
            setMentionStartIndex(null);
        },
        [onChange, updateDropdownPosition]
    );

    const handleKeyDown = useCallback(
        (e: KeyboardEvent<HTMLTextAreaElement>) => {
            if (!showDropdown || activeListLength === 0) return;

            if (e.key === "ArrowDown") {
                e.preventDefault();
                setSelectedIndex((prev) => (prev + 1) % activeListLength);
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setSelectedIndex((prev) => (prev - 1 + activeListLength) % activeListLength);
            } else if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                if (triggerType === "jinja") {
                    insertJinjaSnippet(filteredJinjaSnippets[selectedIndex]);
                } else {
                    insertMention(filteredRecordings[selectedIndex]);
                }
            } else if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                setShowDropdown(false);
                setTriggerType(null);
            }
        },
        [
            showDropdown,
            activeListLength,
            selectedIndex,
            triggerType,
            filteredJinjaSnippets,
            filteredRecordings,
            insertJinjaSnippet,
            insertMention,
        ]
    );

    // When dropdown is active, intercept Escape at window capture phase so it dismisses
    // ONLY the dropdown, preventing the parent modal/dialog from closing.
    useEffect(() => {
        if (!showDropdown || activeListLength === 0) return;

        const handleEscapeCapture = (e: globalThis.KeyboardEvent) => {
            if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                setShowDropdown(false);
                setTriggerType(null);
            }
        };

        window.addEventListener("keydown", handleEscapeCapture, true);
        return () => window.removeEventListener("keydown", handleEscapeCapture, true);
    }, [showDropdown, activeListLength]);

    // Keep dropdown anchored to caret position during textarea scroll or window resize
    useEffect(() => {
        const textarea = textareaRef.current;
        if (!textarea || !showDropdown || mentionStartIndex === null) return;

        const handleScroll = () => {
            updateDropdownPosition(mentionStartIndex);
        };
        const handleResize = () => {
            updateDropdownPosition(mentionStartIndex);
        };

        textarea.addEventListener("scroll", handleScroll);
        window.addEventListener("resize", handleResize);
        return () => {
            textarea.removeEventListener("scroll", handleScroll);
            window.removeEventListener("resize", handleResize);
        };
    }, [showDropdown, mentionStartIndex, updateDropdownPosition]);

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
                setTriggerType(null);
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

    const selectedJinjaSnippet =
        triggerType === "jinja" && filteredJinjaSnippets[selectedIndex]
            ? filteredJinjaSnippets[selectedIndex]
            : null;

    const handleTextareaScroll = useCallback(
        (e: React.UIEvent<HTMLTextAreaElement>) => {
            if (backdropRef.current) {
                backdropRef.current.scrollTop = e.currentTarget.scrollTop;
                backdropRef.current.scrollLeft = e.currentTarget.scrollLeft;
            }
        },
        []
    );

    useEffect(() => {
        if (backdropRef.current && textareaRef.current) {
            backdropRef.current.scrollTop = textareaRef.current.scrollTop;
            backdropRef.current.scrollLeft = textareaRef.current.scrollLeft;
        }
    }, [value]);

    return (
        <div
            className={cn(
                "relative rounded-md transition-all",
                hasJinja && "ring-1 ring-purple-500/35 bg-purple-500/[0.015]"
            )}
        >
            {/* Syntax token highlight backdrop (Highlights ONLY actual Jinja syntax tokens) */}
            {hasJinja && (
                <div
                    ref={backdropRef}
                    aria-hidden="true"
                    className={cn(
                        "pointer-events-none absolute inset-0 overflow-hidden select-none",
                        "font-mono text-xs leading-5 p-3 whitespace-pre-wrap break-words border border-transparent",
                        className
                    )}
                >
                    {tokens.map((token, idx) => {
                        if (token.type === "block") {
                            return (
                                <mark
                                    key={idx}
                                    className="text-transparent rounded-xs bg-purple-500/25 dark:bg-purple-500/35 ring-1 ring-purple-500/40"
                                >
                                    {token.content}
                                </mark>
                            );
                        }
                        if (token.type === "var") {
                            return (
                                <mark
                                    key={idx}
                                    className="text-transparent rounded-xs bg-blue-500/20 dark:bg-blue-500/30 ring-1 ring-blue-500/40"
                                >
                                    {token.content}
                                </mark>
                            );
                        }
                        return (
                            <span key={idx} className="text-transparent">
                                {token.content}
                            </span>
                        );
                    })}
                    {value.endsWith("\n") && (
                        <span className="text-transparent">{"\n"}</span>
                    )}
                </div>
            )}

            <textarea
                ref={textareaRef}
                value={value}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                onScroll={handleTextareaScroll}
                placeholder={placeholder}
                className={cn(
                    "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive flex field-sizing-content min-h-16 w-full rounded-md border p-3 text-xs shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 font-mono leading-5",
                    hasJinja ? "bg-transparent relative z-10" : "bg-transparent dark:bg-input/30",
                    className
                )}
            />

            {/* Jinja Snippets Caret-Anchored Popover */}
            {showDropdown && triggerType === "jinja" && filteredJinjaSnippets.length > 0 && (
                <div
                    ref={dropdownRef}
                    style={{
                        top: `${dropdownPos.top}px`,
                        left: `${dropdownPos.left}px`,
                    }}
                    className={cn(
                        "absolute z-50 w-[420px] max-w-[calc(100vw-32px)] rounded-xl border border-border/80 bg-popover/98 text-popover-foreground shadow-2xl backdrop-blur-md ring-1 ring-black/10 dark:ring-white/10 dark:border-primary/30",
                        "animate-in fade-in-0 zoom-in-95 duration-100 flex flex-col overflow-hidden"
                    )}
                >
                    {/* Popover Header */}
                    <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-2 text-xs">
                        <div className="flex items-center gap-1.5 font-semibold text-foreground">
                            <Sparkles className="h-3.5 w-3.5 text-purple-500" />
                            <span>Jinja Logic Blocks</span>
                        </div>
                        <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                            <kbd className="rounded border border-border/60 bg-background/80 px-1 py-0.5 font-mono">
                                ↑↓
                            </kbd>
                            <kbd className="rounded border border-border/60 bg-background/80 px-1 py-0.5 font-mono">
                                ↵ Insert
                            </kbd>
                            <kbd className="rounded border border-border/60 bg-background/80 px-1 py-0.5 font-mono">
                                Esc
                            </kbd>
                        </div>
                    </div>

                    {/* Snippet List */}
                    <div className="max-h-56 overflow-y-auto p-1.5 space-y-0.5">
                        {filteredJinjaSnippets.map((item, index) => {
                            const isSelected = index === selectedIndex;
                            return (
                                <button
                                    key={item.id}
                                    type="button"
                                    data-selected={isSelected}
                                    className={cn(
                                        "group relative flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition-all cursor-pointer",
                                        isSelected
                                            ? "bg-primary/10 text-foreground dark:bg-primary/15"
                                            : "hover:bg-muted/60 text-muted-foreground hover:text-foreground"
                                    )}
                                    onMouseDown={(e) => {
                                        e.preventDefault();
                                        insertJinjaSnippet(item);
                                    }}
                                    onMouseEnter={() => setSelectedIndex(index)}
                                >
                                    {/* Left active accent line */}
                                    {isSelected && (
                                        <span className="absolute left-0 top-1.5 bottom-1.5 w-1 rounded-r bg-primary" />
                                    )}

                                    {/* Semantic Icon Badge */}
                                    <div
                                        className={cn(
                                            "flex h-7 w-7 shrink-0 items-center justify-center rounded-md border text-xs",
                                            item.category === "condition" &&
                                                "bg-purple-500/15 text-purple-600 dark:text-purple-300 border-purple-500/30",
                                            item.category === "loop" &&
                                                "bg-blue-500/15 text-blue-600 dark:text-blue-300 border-blue-500/30",
                                            item.category === "variable" &&
                                                "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border-emerald-500/30",
                                            item.category === "custom" &&
                                                "bg-amber-500/15 text-amber-600 dark:text-amber-300 border-amber-500/30"
                                        )}
                                    >
                                        {item.category === "loop" ? (
                                            <Repeat className="h-3.5 w-3.5" />
                                        ) : item.category === "variable" ? (
                                            <Code2 className="h-3.5 w-3.5" />
                                        ) : item.category === "custom" ? (
                                            <Sparkles className="h-3.5 w-3.5" />
                                        ) : item.id === "if_else" || item.id === "if_elif" ? (
                                            <Split className="h-3.5 w-3.5" />
                                        ) : (
                                            <GitBranch className="h-3.5 w-3.5" />
                                        )}
                                    </div>

                                    {/* Snippet Details */}
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center justify-between gap-1">
                                            <span className="font-mono text-xs font-semibold text-foreground tracking-tight">
                                                {item.title}
                                            </span>
                                            <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-muted/70 text-muted-foreground">
                                                {item.badge}
                                            </span>
                                        </div>
                                        <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                                            {item.description}
                                        </p>
                                    </div>
                                </button>
                            );
                        })}
                    </div>

                    {/* Live Preview Footer */}
                    {selectedJinjaSnippet && (
                        <div className="border-t border-border/60 bg-muted/20 px-3 py-2">
                            <div className="flex items-center justify-between pb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                                <span>Snippet Preview</span>
                                <span className="text-[10px] text-muted-foreground/70 font-mono">
                                    auto-selects placeholder
                                </span>
                            </div>
                            <pre className="overflow-x-auto rounded-md border border-border/50 bg-background/90 p-2 font-mono text-[11px] leading-relaxed text-foreground/90">
                                {selectedJinjaSnippet.snippet}
                            </pre>
                        </div>
                    )}
                </div>
            )}

            {/* Recordings Caret-Anchored Popover */}
            {showDropdown && triggerType === "recording" && filteredRecordings.length > 0 && (
                <div
                    ref={dropdownRef}
                    style={{
                        top: `${dropdownPos.top}px`,
                        left: `${dropdownPos.left}px`,
                    }}
                    className={cn(
                        "absolute z-50 w-96 max-w-[calc(100vw-32px)] rounded-xl border border-border/80 bg-popover/98 text-popover-foreground shadow-2xl backdrop-blur-md ring-1 ring-black/10 dark:ring-white/10 dark:border-primary/30",
                        "animate-in fade-in-0 zoom-in-95 duration-100 flex flex-col overflow-hidden"
                    )}
                >
                    <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-2 text-xs">
                        <div className="flex items-center gap-1.5 font-semibold text-foreground">
                            <Mic className="h-3.5 w-3.5 text-blue-500" />
                            <span>Audio Recordings</span>
                        </div>
                        <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                            <kbd className="rounded border border-border/60 bg-background/80 px-1 py-0.5 font-mono">
                                ↑↓
                            </kbd>
                            <kbd className="rounded border border-border/60 bg-background/80 px-1 py-0.5 font-mono">
                                ↵ Insert
                            </kbd>
                        </div>
                    </div>
                    <div className="max-h-60 overflow-y-auto p-1.5 space-y-0.5">
                        {filteredRecordings.map((item, index) => {
                            const isSelected = index === selectedIndex;
                            return (
                                <button
                                    key={item.id}
                                    type="button"
                                    data-selected={isSelected}
                                    className={cn(
                                        "flex w-full flex-col gap-0.5 rounded-lg px-2.5 py-2 text-left text-xs transition-all cursor-pointer",
                                        isSelected
                                            ? "bg-primary/10 text-foreground dark:bg-primary/15"
                                            : "hover:bg-muted/60 text-muted-foreground hover:text-foreground"
                                    )}
                                    onMouseDown={(e) => {
                                        e.preventDefault();
                                        insertMention(item);
                                    }}
                                    onMouseEnter={() => setSelectedIndex(index)}
                                >
                                    <div className="flex items-center gap-2">
                                        <code className="text-[11px] bg-muted px-1.5 py-0.5 rounded font-mono text-foreground">
                                            {item.filename}
                                        </code>
                                        <span className="font-medium truncate text-foreground">
                                            {item.name}
                                        </span>
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}

            {showDropdown &&
                triggerType === "recording" &&
                filteredRecordings.length === 0 &&
                recordingItems.length === 0 && (
                    <div
                        style={{
                            top: `${dropdownPos.top}px`,
                            left: `${dropdownPos.left}px`,
                        }}
                        className="absolute z-50 w-80 rounded-xl border border-border/80 bg-popover/98 text-popover-foreground shadow-2xl backdrop-blur-md p-3 text-xs text-muted-foreground"
                    >
                        No recordings found. Upload recordings via the Recordings panel.
                    </div>
                )}
        </div>
    );
}
