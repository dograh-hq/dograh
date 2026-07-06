"use client";

import { useTranslations } from "next-intl";
import { Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ChatComposerProps {
    composerId: string;
    draft: string;
    ready: boolean;
    editing: boolean;
    sendingMessage: boolean;
    inputDisabled: boolean;
    onDraftChange: (value: string) => void;
    onCancelEditing: () => void;
    onSubmit: () => Promise<void> | void;
}

export function ChatComposer({
    composerId,
    draft,
    ready,
    editing,
    sendingMessage,
    inputDisabled,
    onDraftChange,
    onCancelEditing,
    onSubmit,
}: ChatComposerProps) {
    const t = useTranslations("tester");
    return (
        <div className="pt-3">
            {editing ? (
                <div className="mb-2 flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
                    <span>{t("editHint")}</span>
                    <button
                        type="button"
                        onClick={onCancelEditing}
                        className="inline-flex items-center gap-1 rounded text-foreground hover:text-foreground/80 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    >
                        <X className="h-3.5 w-3.5" />
                        {t("cancel")}
                    </button>
                </div>
            ) : null}
            <div className="relative">
                <Textarea
                    id={composerId}
                    value={draft}
                    onChange={(event) => onDraftChange(event.target.value)}
                    placeholder={ready ? (editing ? t("editPlaceholder") : t("sendPlaceholder")) : t("preparing")}
                    rows={1}
                    className="min-h-11! resize-none pr-20 text-sm leading-6"
                    disabled={inputDisabled}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                            event.preventDefault();
                            if (sendingMessage) return;
                            void onSubmit();
                        }
                    }}
                />
                <Button
                    type="button"
                    size="sm"
                    onClick={() => void onSubmit()}
                    disabled={inputDisabled || sendingMessage || !draft.trim()}
                    className="absolute bottom-1.5 right-1.5 h-8 px-4"
                >
                    {sendingMessage ? (
                        <>
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            {editing ? t("rerunning") : t("sending")}
                        </>
                    ) : (
                        editing ? t("rerun") : t("send")
                    )}
                </Button>
            </div>
        </div>
    );
}
