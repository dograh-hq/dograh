"use client";

import { AudioLines, Loader2, Play, RefreshCw, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    clearTtsCacheApiV1TtsCacheDelete,
    invalidateTtsCacheEntryApiV1TtsCacheEntryIdDelete,
    listTtsCacheApiV1TtsCacheGet,
    previewTtsCacheApiV1TtsCacheEntryIdAudioGet,
} from "@/client/sdk.gen";
import type { ListTtsCacheApiV1TtsCacheGetData, TtsCacheEntry } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/dateTime";

const PAGE_SIZE = 25;
const SORT_OPTIONS = [
    { value: "recent", label: "Recently used", sort: "last_used", order: "desc" },
    { value: "most_used", label: "Most reused", sort: "usage", order: "desc" },
    { value: "least_used", label: "Least reused", sort: "usage", order: "asc" },
    { value: "longest", label: "Longest first", sort: "duration", order: "desc" },
    { value: "shortest", label: "Shortest first", sort: "duration", order: "asc" },
] as const;

export default function TtsCacheList() {
    const { user, loading: authLoading } = useAuth();
    const timezone = useOrganizationTimezone();
    const [entries, setEntries] = useState<TtsCacheEntry[]>([]);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState("");
    const [sortValue, setSortValue] = useState("recent");
    const [minDuration, setMinDuration] = useState("");
    const [maxDuration, setMaxDuration] = useState("");
    const [offset, setOffset] = useState(0);
    const [refresh, setRefresh] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [removing, setRemoving] = useState<string | null>(null);
    const [preview, setPreview] = useState<{ id: string; url: string } | null>(null);
    const [previewLoading, setPreviewLoading] = useState<string | null>(null);
    const previewRequest = useRef<AbortController | null>(null);
    const previewUrl = useRef<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);

    const stopPreview = useCallback(() => {
        previewRequest.current?.abort();
        audioRef.current?.pause();
        if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
        previewUrl.current = null;
        setPreview(null);
        setPreviewLoading(null);
    }, []);

    useEffect(() => stopPreview, [stopPreview]);

    const invalidDuration = (minDuration !== "" && (!Number.isFinite(Number(minDuration)) || Number(minDuration) < 0))
        || (maxDuration !== "" && (!Number.isFinite(Number(maxDuration)) || Number(maxDuration) < 0))
        || (minDuration !== "" && maxDuration !== "" && Number(minDuration) > Number(maxDuration));

    useEffect(() => {
        if (authLoading || !user) return;
        stopPreview();
        if (invalidDuration) {
            setError("Enter finite, non-negative durations: minimum must not exceed maximum.");
            setLoading(false);
            return;
        }
        const controller = new AbortController();
        setLoading(true);
        setError(null);
        const timer = setTimeout(async () => {
            const selected = SORT_OPTIONS.find((option) => option.value === sortValue) ?? SORT_OPTIONS[0];
            const query: ListTtsCacheApiV1TtsCacheGetData["query"] = {
                search, sort: selected.sort, order: selected.order,
                min_duration: minDuration === "" ? undefined : Number(minDuration),
                max_duration: maxDuration === "" ? undefined : Number(maxDuration),
                offset, limit: PAGE_SIZE,
            };
            try {
                const result = await listTtsCacheApiV1TtsCacheGet({ query, signal: controller.signal });
                if (controller.signal.aborted) return;
                if (result.error || !result.data) {
                    setError(detailFromError(result.error, "Could not load cached speech."));
                    setEntries([]);
                    return;
                }
                if (offset > 0 && offset >= result.data.total) {
                    setOffset(Math.max(0, Math.ceil(result.data.total / PAGE_SIZE) - 1) * PAGE_SIZE);
                    return;
                }
                setEntries(result.data.entries);
                setTotal(result.data.total);
            } catch {
                if (!controller.signal.aborted) setError("Could not load cached speech.");
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        }, 250);
        return () => { clearTimeout(timer); controller.abort(); };
    }, [authLoading, user, search, sortValue, minDuration, maxDuration, invalidDuration, offset, refresh, stopPreview]);

    const handlePreview = async (entry: TtsCacheEntry) => {
        stopPreview();
        const controller = new AbortController();
        previewRequest.current = controller;
        setPreviewLoading(entry.id);
        try {
            const result = await previewTtsCacheApiV1TtsCacheEntryIdAudioGet({
                path: { entry_id: entry.id }, parseAs: "blob", signal: controller.signal,
            });
            if (controller.signal.aborted) return;
            if (result.error || !result.data) {
                toast.error(detailFromError(result.error, "This cached audio may have expired. Refresh and try again."));
                return;
            }
            const url = URL.createObjectURL(result.data);
            previewUrl.current = url;
            setPreview({ id: entry.id, url });
        } catch {
            if (!controller.signal.aborted) toast.error("Could not play cached speech.");
        } finally {
            if (!controller.signal.aborted) setPreviewLoading(null);
        }
    };

    const handleInvalidate = async (entry?: TtsCacheEntry) => {
        const message = entry
            ? "Invalidate this cached speech? It will be synthesized again when next requested."
            : "Clear all cached speech for your organization? Speech will be synthesized again when requested.";
        if (!window.confirm(message)) return;
        stopPreview();
        setRemoving(entry?.id ?? "all");
        try {
            const result = entry
                ? await invalidateTtsCacheEntryApiV1TtsCacheEntryIdDelete({ path: { entry_id: entry.id } })
                : await clearTtsCacheApiV1TtsCacheDelete();
            if (result.error) {
                toast.error(detailFromError(result.error, "Could not invalidate cached speech."));
                return;
            }
            toast.success(entry ? "Cached speech invalidated" : "Organization speech cache cleared");
            setRefresh((value) => value + 1);
        } catch {
            toast.error("Could not invalidate cached speech.");
        } finally {
            setRemoving(null);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
                <div className="relative min-w-48 flex-1">
                    <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                    <Input aria-label="Search cached speech" placeholder="Search phrase, model, or voice…" maxLength={200}
                        className="pl-9" value={search} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} />
                </div>
                <div>
                    <label htmlFor="tts-cache-sort" className="mb-1 block text-xs text-muted-foreground">Sort by</label>
                    <select id="tts-cache-sort" value={sortValue} onChange={(e) => { setSortValue(e.target.value); setOffset(0); }}
                        className="h-9 rounded-md border bg-background px-3 text-sm">
                        {SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                </div>
                <div className="w-28">
                    <label htmlFor="tts-min-duration" className="mb-1 block text-xs text-muted-foreground">Min seconds</label>
                    <Input id="tts-min-duration" type="number" min="0" step="0.1" value={minDuration}
                        onChange={(e) => { setMinDuration(e.target.value); setOffset(0); }} placeholder="Any" />
                </div>
                <div className="w-28">
                    <label htmlFor="tts-max-duration" className="mb-1 block text-xs text-muted-foreground">Max seconds</label>
                    <Input id="tts-max-duration" type="number" min="0" step="0.1" value={maxDuration}
                        onChange={(e) => { setMaxDuration(e.target.value); setOffset(0); }} placeholder="Any" />
                </div>
                <Button variant="outline" size="icon" aria-label="Refresh cached speech" disabled={loading}
                    onClick={() => setRefresh((value) => value + 1)}>
                    <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                </Button>
                <Button variant="outline" disabled={loading || removing !== null} onClick={() => handleInvalidate()}>
                    <Trash2 className="mr-2 h-4 w-4" />{removing === "all" ? "Clearing…" : "Clear organization cache"}
                </Button>
            </div>
            <p className="text-xs text-muted-foreground">
                Reuses count speech requests served from cache. Listening here does not count as reuse or extend expiry.
            </p>
            {error ? <div role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-destructive">{error}</div>
                : loading ? <Skeleton className="h-40 w-full" />
                    : entries.length === 0 ? <div className="py-12 text-center text-muted-foreground">
                        <AudioLines className="mx-auto mb-3 h-10 w-10" />
                        <p>No cached speech found.</p>
                        <p className="mt-1 text-sm">Speech appears here after a successful synthesis in a workflow with Speech Caching enabled.</p>
                    </div>
                        : <>
                            <p className="text-sm text-muted-foreground">{total} cached phrase{total === 1 ? "" : "s"} matching your filters</p>
                            <div className="space-y-3">
                                {entries.map((entry) => (
                                    <div key={entry.id} className="rounded-lg border p-4">
                                        <div className="flex flex-wrap items-start justify-between gap-3">
                                            <div className="min-w-0 flex-1">
                                                <p className="whitespace-pre-wrap break-words text-sm">{entry.text_preview || "Cached speech"}</p>
                                                <p className="mt-1 break-words text-xs text-muted-foreground">{[entry.provider, entry.model, entry.voice_id].filter(Boolean).join(" · ")}</p>
                                                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                                                    <span>{entry.duration_seconds.toFixed(2)} seconds</span>
                                                    <span>{entry.hit_count.toLocaleString()} reuses</span>
                                                    <span>Last used {formatDateTime(entry.last_used_at, timezone)}</span>
                                                </div>
                                            </div>
                                            <div className="flex gap-2">
                                                <Button variant="outline" size="sm" disabled={previewLoading === entry.id || removing !== null}
                                                    aria-label={`Listen to ${entry.text_preview || "cached speech"}`} onClick={() => handlePreview(entry)}>
                                                    {previewLoading === entry.id ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}Listen
                                                </Button>
                                                <Button variant="ghost" size="sm" className="text-destructive" disabled={removing !== null}
                                                    aria-label={`Invalidate ${entry.text_preview || "cached speech"}`} onClick={() => handleInvalidate(entry)}>
                                                    <Trash2 className="mr-2 h-4 w-4" />{removing === entry.id ? "Invalidating…" : "Invalidate"}
                                                </Button>
                                            </div>
                                        </div>
                                        {preview?.id === entry.id && <audio ref={audioRef} controls autoPlay src={preview.url}
                                            className="mt-3 w-full" aria-label="Cached speech preview" onError={() => toast.error("Could not play cached speech.")} />}
                                    </div>
                                ))}
                            </div>
                            <div className="flex items-center justify-between">
                                <span className="text-sm text-muted-foreground">{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
                                <div className="flex gap-2">
                                    <Button variant="outline" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>Previous</Button>
                                    <Button variant="outline" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>Next</Button>
                                </div>
                            </div>
                        </>}
        </div>
    );
}
