"use client";

import { formatDistanceToNow } from "date-fns";
import { FileText, LoaderCircle, X } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export interface WorkflowVersion {
    id: number;
    version_number: number;
    status: string;
    created_at: string;
    published_at: string | null;
    workflow_json: { nodes?: unknown[]; edges?: unknown[]; viewport?: unknown };
    workflow_configurations: Record<string, unknown> | null;
    template_context_variables: Record<string, string> | null;
}

interface VersionHistoryPanelProps {
    isOpen: boolean;
    onClose: () => void;
    versions: WorkflowVersion[];
    loading: boolean;
    activeVersionId: number | null;
    onSelectVersion: (version: WorkflowVersion) => void;
    hasMore: boolean;
    loadingMore: boolean;
    onLoadMore: () => void;
}

const statusLabel: Record<string, string> = {
    draft: "Draft",
    published: "Published",
    archived: "Archived",
};

const statusColor: Record<string, string> = {
    draft: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
    published: "bg-green-500/20 text-green-400 border-green-500/30",
    archived: "bg-gray-500/20 text-gray-400 border-gray-500/30",
};

export const VersionHistoryPanel = ({
    isOpen,
    onClose,
    versions,
    loading,
    activeVersionId,
    onSelectVersion,
    hasMore,
    loadingMore,
    onLoadMore,
}: VersionHistoryPanelProps) => {
    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape" && isOpen) {
                onClose();
            }
        };
        document.addEventListener("keydown", handleKeyDown);
        return () => document.removeEventListener("keydown", handleKeyDown);
    }, [isOpen, onClose]);

    return (
        <div
            className={`fixed z-51 right-0 top-0 h-full w-80 bg-[#1a1a1a] border-l border-[#2a2a2a] shadow-lg transform transition-transform duration-300 ease-in-out ${
                isOpen ? "translate-x-0" : "translate-x-full"
            }`}
        >
            <div className="p-4 h-full overflow-y-auto">
                <div className="flex justify-between items-center mb-6">
                    <h2 className="text-lg font-semibold text-white">
                        Version History
                    </h2>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={onClose}
                        className="text-gray-400 hover:text-white hover:bg-[#2a2a2a]"
                    >
                        <X className="w-5 h-5" />
                    </Button>
                </div>

                {loading ? (
                    <div className="flex items-center justify-center py-12">
                        <LoaderCircle className="w-6 h-6 text-gray-400 animate-spin" />
                    </div>
                ) : versions.length === 0 ? (
                    <p className="text-sm text-gray-500 text-center py-8">
                        No versions found.
                    </p>
                ) : (
                    <div className="space-y-2">
                        {versions.map((version) => {
                            const isActive = version.id === activeVersionId;
                            const date = version.published_at || version.created_at;
                            return (
                                <button
                                    key={version.id}
                                    onClick={() => onSelectVersion(version)}
                                    className={`w-full text-left p-3 rounded-lg border transition-colors cursor-pointer ${
                                        isActive
                                            ? "border-teal-500/50 bg-teal-500/10"
                                            : "border-[#2a2a2a] bg-[#222] hover:bg-[#2a2a2a]"
                                    }`}
                                >
                                    <div className="flex items-center justify-between mb-1.5">
                                        <div className="flex items-center gap-2">
                                            <FileText className="w-4 h-4 text-gray-400" />
                                            <span className="text-sm font-medium text-white">
                                                v{version.version_number}
                                            </span>
                                        </div>
                                        {version.status !== "archived" && (
                                            <span
                                                className={`text-xs px-2 py-0.5 rounded-full border ${
                                                    statusColor[version.status] ?? ""
                                                }`}
                                            >
                                                {statusLabel[version.status] ?? version.status}
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-xs text-gray-500">
                                        {formatDistanceToNow(new Date(date), {
                                            addSuffix: true,
                                        })}
                                    </p>
                                </button>
                            );
                        })}
                        {hasMore && (
                            <Button
                                variant="ghost"
                                onClick={onLoadMore}
                                disabled={loadingMore}
                                className="w-full text-sm text-gray-300 hover:text-white hover:bg-[#2a2a2a]"
                            >
                                {loadingMore ? (
                                    <LoaderCircle className="w-4 h-4 animate-spin" />
                                ) : (
                                    "Load more"
                                )}
                            </Button>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};
