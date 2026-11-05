import { Code2, GitBranch, Info, PenLine, Repeat, Sparkles, Split } from "lucide-react";
import React, { useMemo } from "react";

import { cn } from "@/lib/utils";

export interface MarkdownBlock {
    type: "markdown";
    content: string;
}

export interface ConditionalBranch {
    kind: "if" | "elif" | "else";
    condition?: string;
    content: string;
}

export interface ConditionalBlock {
    type: "conditional";
    condition: string;
    ifContent: string;
    elseContent: string | null;
    branches: ConditionalBranch[];
}

export interface LoopBlock {
    type: "loop";
    item: string;
    items: string;
    content: string;
    elseContent?: string | null;
}

export interface SetVariableBlock {
    type: "set";
    varName: string;
    expression: string;
}

export interface CustomTagBlock {
    type: "custom_tag";
    tagContent: string;
}

export type PreviewBlock =
    | MarkdownBlock
    | ConditionalBlock
    | LoopBlock
    | SetVariableBlock
    | CustomTagBlock;

/**
 * Splits prompt text into Markdown sections and Jinja logic blocks.
 */
export function parsePromptToPreviewBlocks(text: string): PreviewBlock[] {
    if (!text) return [];
    const lines = text.split("\n");
    const blocks: PreviewBlock[] = [];
    let currentMarkdown: string[] = [];
    let i = 0;

    const flushMarkdown = () => {
        if (currentMarkdown.length > 0) {
            blocks.push({ type: "markdown", content: currentMarkdown.join("\n") });
            currentMarkdown = [];
        }
    };

    while (i < lines.length) {
        const line = lines[i];
        const trimmedLine = line.trim();

        const ifMatch = trimmedLine.match(/^\{%-?\s*if\s+(.+?)\s*-?%\}$/);
        const forMatch = trimmedLine.match(/^\{%-?\s*for\s+([^%]+?)\s+in\s+(.+?)\s*-?%\}$/);

        if (ifMatch) {
            flushMarkdown();

            const branches: ConditionalBranch[] = [];
            let currentKind: "if" | "elif" | "else" = "if";
            let currentCondition: string | undefined = ifMatch[1].trim();
            let currentLines: string[] = [];
            const blockStack: ("if" | "for")[] = ["if"];
            i++;

            while (i < lines.length && blockStack.length > 0) {
                const subLine = lines[i];
                const trimmedSub = subLine.trim();

                const isNestedIf = /^\{%-?\s*if\s(?:(?!%\}).)*-?%\}$/.test(trimmedSub);
                const isNestedFor = /^\{%-?\s*for\s+(?:(?!%\}).)*-?%\}$/.test(trimmedSub);
                const isEndif = /^\{%-?\s*endif\s*-?%\}$/.test(trimmedSub);
                const isEndfor = /^\{%-?\s*endfor\s*-?%\}$/.test(trimmedSub);
                const elifMatch = trimmedSub.match(/^\{%-?\s*elif\s+(.+?)\s*-?%\}$/);
                const isElse = /^\{%-?\s*else\s*-?%\}$/.test(trimmedSub);

                if (blockStack.length === 1 && blockStack[0] === "if") {
                    if (elifMatch) {
                        branches.push({
                            kind: currentKind,
                            condition: currentCondition,
                            content: currentLines.join("\n").trim(),
                        });
                        currentKind = "elif";
                        currentCondition = elifMatch[1].trim();
                        currentLines = [];
                    } else if (isElse) {
                        branches.push({
                            kind: currentKind,
                            condition: currentCondition,
                            content: currentLines.join("\n").trim(),
                        });
                        currentKind = "else";
                        currentCondition = undefined;
                        currentLines = [];
                    } else if (isEndif) {
                        branches.push({
                            kind: currentKind,
                            condition: currentCondition,
                            content: currentLines.join("\n").trim(),
                        });
                        blockStack.pop();
                    } else {
                        currentLines.push(subLine);
                        if (isNestedIf) {
                            blockStack.push("if");
                        } else if (isNestedFor) {
                            blockStack.push("for");
                        }
                    }
                } else {
                    currentLines.push(subLine);
                    if (isNestedIf) {
                        blockStack.push("if");
                    } else if (isNestedFor) {
                        blockStack.push("for");
                    } else if (isEndif && blockStack[blockStack.length - 1] === "if") {
                        blockStack.pop();
                    } else if (isEndfor && blockStack[blockStack.length - 1] === "for") {
                        blockStack.pop();
                    }
                }
                i++;
            }

            const ifBranch = branches.find((b) => b.kind === "if");
            const elseBranch = branches.find((b) => b.kind === "else");

            blocks.push({
                type: "conditional",
                condition: ifBranch?.condition || ifMatch[1].trim(),
                ifContent: ifBranch?.content || "",
                elseContent: elseBranch?.content ?? null,
                branches,
            });
            continue;
        }

        if (forMatch) {
            flushMarkdown();
            const item = forMatch[1].trim();
            const items = forMatch[2].trim();
            const forLines: string[] = [];
            const elseLines: string[] = [];
            let inElse = false;
            const blockStack: ("if" | "for")[] = ["for"];
            i++;

            while (i < lines.length && blockStack.length > 0) {
                const subLine = lines[i];
                const trimmedSub = subLine.trim();

                const isNestedFor = /^\{%-?\s*for\s+(?:(?!%\}).)*-?%\}$/.test(trimmedSub);
                const isNestedIf = /^\{%-?\s*if\s(?:(?!%\}).)*-?%\}$/.test(trimmedSub);
                const isEndfor = /^\{%-?\s*endfor\s*-?%\}$/.test(trimmedSub);
                const isEndif = /^\{%-?\s*endif\s*-?%\}$/.test(trimmedSub);
                const isElse = /^\{%-?\s*else\s*-?%\}$/.test(trimmedSub);

                if (blockStack.length === 1 && blockStack[0] === "for") {
                    if (isElse) {
                        inElse = true;
                    } else if (isEndfor) {
                        blockStack.pop();
                    } else {
                        if (inElse) elseLines.push(subLine);
                        else forLines.push(subLine);

                        if (isNestedFor) {
                            blockStack.push("for");
                        } else if (isNestedIf) {
                            blockStack.push("if");
                        }
                    }
                } else {
                    if (inElse) elseLines.push(subLine);
                    else forLines.push(subLine);

                    if (isNestedFor) {
                        blockStack.push("for");
                    } else if (isNestedIf) {
                        blockStack.push("if");
                    } else if (isEndfor && blockStack[blockStack.length - 1] === "for") {
                        blockStack.pop();
                    } else if (isEndif && blockStack[blockStack.length - 1] === "if") {
                        blockStack.pop();
                    }
                }
                i++;
            }

            blocks.push({
                type: "loop",
                item,
                items,
                content: forLines.join("\n").trim(),
                elseContent: inElse ? elseLines.join("\n").trim() : null,
            });
            continue;
        }

        const setMatch = trimmedLine.match(/^\{%-?\s*set\s+([^%=]+?)\s*=\s*(.+?)\s*-?%\}$/);
        if (setMatch) {
            flushMarkdown();
            blocks.push({
                type: "set",
                varName: setMatch[1],
                expression: setMatch[2].trim(),
            });
            i++;
            continue;
        }

        const customTagMatch = trimmedLine.match(/^\{%\s*(.+?)\s*%\}/);
        if (customTagMatch) {
            flushMarkdown();
            blocks.push({
                type: "custom_tag",
                tagContent: customTagMatch[1].trim(),
            });
            i++;
            continue;
        }

        currentMarkdown.push(line);
        i++;
    }

    flushMarkdown();

    return blocks;
}

/**
 * Renders inline formatting: **bold**, `code`, and {{ variables }}.
 */
function renderInlineFormatting(text: string): React.ReactNode {
    // Split by {{ ... }}, {% ... %}, **...**, and `...`
    const regex = /(\{\{.*?\}\}|\{%.*?%\}|\*\*.*?\*\*|`.*?`)/g;
    const parts = text.split(regex);

    return parts.map((part, idx) => {
        if (part.startsWith("{{") && part.endsWith("}}")) {
            const varName = part.slice(2, -2).trim();
            return (
                <code
                    key={idx}
                    className="inline-flex items-center rounded bg-blue-500/10 px-1 py-0.5 font-mono text-[11px] text-blue-700 dark:text-blue-300 border border-blue-500/30 font-medium mx-0.5"
                >
                    <span className="text-blue-500/60 font-mono">{"{{"}</span>
                    {varName}
                    <span className="text-blue-500/60 font-mono">{"}}"}</span>
                </code>
            );
        }
        if (part.startsWith("{%") && part.endsWith("%}")) {
            return (
                <code
                    key={idx}
                    className="inline-flex items-center rounded bg-purple-500/10 px-1 py-0.5 font-mono text-[11px] text-purple-700 dark:text-purple-300 border border-purple-500/30 font-medium mx-0.5"
                >
                    {part}
                </code>
            );
        }
        if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
            return (
                <strong key={idx} className="font-semibold text-foreground">
                    {part.slice(2, -2)}
                </strong>
            );
        }
        if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
            return (
                <code
                    key={idx}
                    className="rounded bg-muted px-1 py-0.5 font-mono text-[11px] text-foreground border border-border/40 mx-0.5"
                >
                    {part.slice(1, -1)}
                </code>
            );
        }
        return <span key={idx}>{part}</span>;
    });
}

/**
 * Renders a markdown block (headers, bullet lists, numbered lists, paragraphs).
 */
export function MarkdownSection({ content }: { content: string }) {
    const lines = useMemo(() => content.split("\n"), [content]);

    return (
        <div className="space-y-1">
            {lines.map((line, idx) => {
                const trimmed = line.trim();
                if (!trimmed) {
                    return <div key={idx} className="h-1.5" />;
                }

                // H1: # Title
                if (trimmed.startsWith("# ")) {
                    return (
                        <h3
                            key={idx}
                            className="text-xs font-bold text-foreground mt-2 mb-1 pb-0.5 border-b border-border/40 first:mt-0 tracking-wide uppercase font-mono"
                        >
                            {renderInlineFormatting(trimmed.slice(2))}
                        </h3>
                    );
                }

                // H2: ## Subtitle
                if (trimmed.startsWith("## ")) {
                    return (
                        <h4
                            key={idx}
                            className="text-xs font-semibold text-foreground mt-1.5 mb-0.5 first:mt-0"
                        >
                            {renderInlineFormatting(trimmed.slice(3))}
                        </h4>
                    );
                }

                // H3: ### Header
                if (trimmed.startsWith("### ")) {
                    return (
                        <h5
                            key={idx}
                            className="text-[11px] font-medium text-foreground mt-1 mb-0.5 first:mt-0"
                        >
                            {renderInlineFormatting(trimmed.slice(4))}
                        </h5>
                    );
                }

                // Numbered list item: 1. Item
                const numMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
                if (numMatch) {
                    return (
                        <div key={idx} className="flex items-start gap-1.5 ml-2 text-xs leading-relaxed">
                            <span className="font-mono text-muted-foreground shrink-0 text-[11px]">
                                {numMatch[1]}.
                            </span>
                            <span className="text-foreground/90">
                                {renderInlineFormatting(numMatch[2])}
                            </span>
                        </div>
                    );
                }

                // Bullet list item: - Item or * Item
                const bulletMatch = trimmed.match(/^[-*]\s+(.*)/);
                if (bulletMatch) {
                    return (
                        <div key={idx} className="flex items-start gap-2 ml-2 text-xs leading-relaxed">
                            <span className="text-muted-foreground/80 shrink-0 text-sm leading-4">
                                •
                            </span>
                            <span className="text-foreground/90">
                                {renderInlineFormatting(bulletMatch[1])}
                            </span>
                        </div>
                    );
                }

                // Regular line / paragraph
                return (
                    <p key={idx} className="text-xs leading-relaxed text-foreground/90">
                        {renderInlineFormatting(line)}
                    </p>
                );
            })}
        </div>
    );
}

interface PromptPreviewProps {
    value: string;
    className?: string;
    onSwitchToWrite?: () => void;
}

export function PromptPreview({ value, className, onSwitchToWrite }: PromptPreviewProps) {
    const blocks = useMemo(() => parsePromptToPreviewBlocks(value), [value]);

    if (!value.trim()) {
        return (
            <div
                onClick={onSwitchToWrite}
                className={cn(
                    "min-h-[100px] max-h-[300px] overflow-y-auto rounded-md border border-input bg-card/40 p-4 text-xs text-muted-foreground italic flex flex-col items-center justify-center gap-2",
                    onSwitchToWrite && "cursor-pointer hover:bg-card/60 transition-colors",
                    className
                )}
            >
                <span>No prompt content to preview.</span>
                {onSwitchToWrite && (
                    <button
                        type="button"
                        onClick={onSwitchToWrite}
                        className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline not-italic cursor-pointer"
                    >
                        <PenLine className="h-3 w-3" />
                        Click to write prompt
                    </button>
                )}
            </div>
        );
    }

    const handleContainerClick = () => {
        // If user is selecting text to copy, don't trigger switch
        const selection = typeof window !== "undefined" ? window.getSelection() : null;
        if (selection && selection.toString().length > 0) return;
        onSwitchToWrite?.();
    };

    return (
        <div className="space-y-1.5">
            {/* Click-to-edit banner hint */}
            {onSwitchToWrite && (
                <div
                    role="button"
                    tabIndex={0}
                    onClick={onSwitchToWrite}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onSwitchToWrite();
                        }
                    }}
                    className="group flex items-center justify-between gap-2 rounded-md border border-border/70 bg-muted/40 hover:bg-muted/70 px-2.5 py-1 text-[11px] text-muted-foreground hover:text-foreground transition-all cursor-pointer select-none"
                >
                    <div className="flex items-center gap-1.5">
                        <Info className="h-3.5 w-3.5 text-primary shrink-0" />
                        <span>Preview is read-only. Click anywhere to edit.</span>
                    </div>
                    <span className="inline-flex items-center gap-1 font-medium text-primary text-[11px] group-hover:underline shrink-0">
                        <PenLine className="h-3 w-3" />
                        Switch to Write
                    </span>
                </div>
            )}

            <div
                onClick={handleContainerClick}
                title={onSwitchToWrite ? "Click anywhere to edit in Write mode" : undefined}
                className={cn(
                    "min-h-[100px] max-h-[300px] overflow-y-auto rounded-md border border-input bg-card/60 p-3 text-xs leading-relaxed font-sans space-y-2 select-text",
                    onSwitchToWrite && "cursor-pointer hover:border-primary/40 transition-colors",
                    className
                )}
            >
                {blocks.map((block, idx) => {
                if (block.type === "markdown") {
                    return <MarkdownSection key={idx} content={block.content} />;
                }

                if (block.type === "conditional") {
                    return (
                        <div
                            key={idx}
                            className="my-2 rounded-lg border border-purple-500/30 bg-purple-500/[0.04] dark:bg-purple-500/[0.07] overflow-hidden text-xs"
                        >
                            {block.branches.map((branch, bIdx) => (
                                <div
                                    key={bIdx}
                                    className={bIdx > 0 ? "border-t border-purple-500/20" : undefined}
                                >
                                    {branch.kind === "if" && (
                                        <div className="flex items-center gap-1.5 bg-purple-500/10 px-2.5 py-1 text-[11px] font-mono font-medium text-purple-700 dark:text-purple-300 border-b border-purple-500/20">
                                            <GitBranch className="h-3 w-3 shrink-0" />
                                            <span>IF: {branch.condition}</span>
                                        </div>
                                    )}
                                    {branch.kind === "elif" && (
                                        <div className="flex items-center gap-1.5 bg-purple-500/15 px-2.5 py-1 text-[11px] font-mono font-medium text-purple-700 dark:text-purple-300 border-b border-purple-500/20">
                                            <Split className="h-3 w-3 shrink-0" />
                                            <span>ELIF: {branch.condition}</span>
                                        </div>
                                    )}
                                    {branch.kind === "else" && (
                                        <div className="flex items-center gap-1.5 bg-muted/60 px-2.5 py-1 text-[11px] font-mono font-medium text-muted-foreground border-b border-border/40">
                                            <Split className="h-3 w-3 shrink-0" />
                                            <span>ELSE (Fallback)</span>
                                        </div>
                                    )}
                                    <div className={cn("p-2.5", branch.kind === "else" && "bg-muted/15")}>
                                        <MarkdownSection content={branch.content} />
                                    </div>
                                </div>
                            ))}
                        </div>
                    );
                }

                if (block.type === "loop") {
                    return (
                        <div
                            key={idx}
                            className="my-2 rounded-lg border border-blue-500/30 bg-blue-500/[0.04] dark:bg-blue-500/[0.07] overflow-hidden text-xs"
                        >
                            <div className="flex items-center gap-1.5 bg-blue-500/10 px-2.5 py-1 text-[11px] font-mono font-medium text-blue-700 dark:text-blue-300 border-b border-blue-500/20">
                                <Repeat className="h-3 w-3 shrink-0" />
                                <span>
                                    FOR: {block.item} IN {block.items}
                                </span>
                            </div>
                            <div className="p-2.5">
                                <MarkdownSection content={block.content} />
                            </div>
                            {block.elseContent && (
                                <div className="border-t border-border/40">
                                    <div className="flex items-center gap-1.5 bg-muted/60 px-2.5 py-1 text-[11px] font-mono font-medium text-muted-foreground border-b border-border/40">
                                        <Split className="h-3 w-3 shrink-0" />
                                        <span>ELSE (If empty)</span>
                                    </div>
                                    <div className="p-2.5 bg-muted/15">
                                        <MarkdownSection content={block.elseContent} />
                                    </div>
                                </div>
                            )}
                        </div>
                    );
                }

                if (block.type === "set") {
                    return (
                        <div
                            key={idx}
                            className="my-1.5 inline-flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/[0.06] dark:bg-emerald-500/[0.1] px-2.5 py-1 text-xs font-mono"
                        >
                            <div className="flex items-center gap-1 text-emerald-700 dark:text-emerald-300 font-semibold">
                                <Code2 className="h-3 w-3 shrink-0" />
                                <span>SET:</span>
                            </div>
                            <span className="font-semibold text-foreground">{block.varName}</span>
                            <span className="text-muted-foreground">=</span>
                            <span className="text-foreground/90">{block.expression}</span>
                        </div>
                    );
                }

                if (block.type === "custom_tag") {
                    return (
                        <div
                            key={idx}
                            className="my-1.5 inline-flex items-center gap-1.5 rounded-md border border-purple-500/30 bg-purple-500/[0.06] dark:bg-purple-500/[0.1] px-2.5 py-1 text-xs font-mono text-purple-700 dark:text-purple-300"
                        >
                            <Sparkles className="h-3 w-3 shrink-0" />
                            <span>{`{% ${block.tagContent} %}`}</span>
                        </div>
                    );
                }

                return null;
            })}
            </div>
        </div>
    );
}
