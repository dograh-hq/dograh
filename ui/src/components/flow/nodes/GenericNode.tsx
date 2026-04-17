import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import * as LucideIcons from "lucide-react";
import { Check, Circle, Copy, Edit, type LucideIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { NodeSpec } from "@/client/types.gen";
import { DocumentBadges } from "@/components/flow/DocumentBadges";
import { NodeEditForm, useNodeSpecs } from "@/components/flow/renderer";
import { ToolBadges } from "@/components/flow/ToolBadges";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { NODE_DOCUMENTATION_URLS } from "@/constants/documentation";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

// ─── Static per-spec UI maps ──────────────────────────────────────────────
// Small lookups indexed by spec.name. Keeping these in the renderer (not
// the spec) avoids leaking UI concerns into the backend schema. Add an
// entry when registering a new node type.

type NodeStyleVariant =
    | "start"
    | "agent"
    | "end"
    | "global"
    | "trigger"
    | "webhook"
    | "qa";

const STYLE_VARIANT_BY_SPEC: Record<string, NodeStyleVariant> = {
    startCall: "start",
    agentNode: "agent",
    endCall: "end",
    globalNode: "global",
    trigger: "trigger",
    webhook: "webhook",
    qa: "qa",
};

const HANDLES_BY_SPEC: Record<string, { source: boolean; target: boolean }> = {
    startCall: { source: true, target: false },
    agentNode: { source: true, target: true },
    endCall: { source: false, target: true },
    globalNode: { source: false, target: false },
    trigger: { source: false, target: false },
    webhook: { source: false, target: false },
    qa: { source: false, target: false },
};

const DOC_URL_BY_SPEC: Record<string, string | undefined> = {
    startCall: NODE_DOCUMENTATION_URLS.startCall,
    agentNode: NODE_DOCUMENTATION_URLS.agent,
    endCall: NODE_DOCUMENTATION_URLS.endCall,
    globalNode: NODE_DOCUMENTATION_URLS.global,
    trigger: NODE_DOCUMENTATION_URLS.apiTrigger,
    webhook: NODE_DOCUMENTATION_URLS.webhook,
    qa: NODE_DOCUMENTATION_URLS.qaAnalysis,
};

// ─── Helpers ──────────────────────────────────────────────────────────────

function resolveIcon(name: string): LucideIcon {
    const icons = LucideIcons as unknown as Record<string, LucideIcon>;
    return icons[name] ?? Circle;
}

function seedValues(
    data: FlowNodeData,
    spec: NodeSpec,
): Record<string, unknown> {
    const d = data as unknown as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const prop of spec.properties) {
        out[prop.name] = d[prop.name] ?? prop.default ?? undefined;
    }
    return out;
}

function buildTriggerEndpoint(triggerPath: string | undefined): string {
    if (!triggerPath) return "";
    const backendUrl =
        process.env.NEXT_PUBLIC_BACKEND_URL ||
        (typeof window !== "undefined" ? window.location.origin : "");
    return `${backendUrl}/api/v1/public/agent/${triggerPath}`;
}

// ─── Canvas preview dispatch ──────────────────────────────────────────────

function CanvasPreview({
    spec,
    data,
    onCopyTrigger,
    triggerCopied,
    onStaleTools,
    onStaleDocuments,
}: {
    spec: NodeSpec;
    data: FlowNodeData;
    onCopyTrigger: () => void;
    triggerCopied: boolean;
    onStaleTools: (uuids: string[]) => void;
    onStaleDocuments: (uuids: string[]) => void;
}) {
    if (spec.name === "trigger") {
        const endpoint = buildTriggerEndpoint(data.trigger_path);
        return (
            <div className="space-y-2">
                <p className="text-xs text-muted-foreground">API Endpoint:</p>
                <div className="flex items-center gap-1">
                    <code className="text-xs break-all bg-muted px-1 py-0.5 rounded flex-1">
                        {endpoint || "Generating..."}
                    </code>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 shrink-0"
                        onClick={(e) => {
                            e.stopPropagation();
                            onCopyTrigger();
                        }}
                    >
                        {triggerCopied ? (
                            <Check className="h-3 w-3" />
                        ) : (
                            <Copy className="h-3 w-3" />
                        )}
                    </Button>
                </div>
            </div>
        );
    }

    if (spec.name === "webhook") {
        const method = data.http_method || "POST";
        const url = data.endpoint_url || "";
        const enabled = data.enabled !== false;
        const truncated = !url
            ? "Not configured"
            : url.length > 30
            ? url.slice(0, 30) + "..."
            : url;
        return (
            <div className="space-y-2">
                <div className="flex items-center gap-2">
                    <span className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">
                        {method}
                    </span>
                    <span className="text-xs text-muted-foreground truncate flex-1">
                        {truncated}
                    </span>
                </div>
                <StatusDot enabled={enabled} />
            </div>
        );
    }

    if (spec.name === "qa") {
        const llmSource =
            data.qa_use_workflow_llm !== false
                ? "Workflow LLM"
                : `${data.qa_provider || "openai"}/${data.qa_model || "gpt-4.1"}`;
        const enabled = data.qa_enabled !== false;
        return (
            <div className="space-y-2">
                <div className="flex items-center gap-2">
                    <span className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">
                        {llmSource}
                    </span>
                </div>
                <StatusDot enabled={enabled} />
            </div>
        );
    }

    // Default: prompt preview + tool/document badges (when spec declares them).
    const hasToolRefs = spec.properties.some((p) => p.type === "tool_refs");
    const hasDocRefs = spec.properties.some((p) => p.type === "document_refs");
    return (
        <>
            <p className="text-sm text-muted-foreground line-clamp-5 leading-relaxed">
                {data.prompt || "No prompt configured"}
            </p>
            {hasToolRefs && data.tool_uuids && data.tool_uuids.length > 0 && (
                <div className="mt-3 pt-3 border-t border-border/50">
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-2">
                        <LucideIcons.Wrench className="h-3 w-3" />
                        <span>Tools:</span>
                    </div>
                    <ToolBadges
                        toolUuids={data.tool_uuids}
                        onStaleUuidsDetected={onStaleTools}
                    />
                </div>
            )}
            {hasDocRefs && data.document_uuids && data.document_uuids.length > 0 && (
                <div className="mt-3 pt-3 border-t border-border/50">
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-2">
                        <LucideIcons.FileText className="h-3 w-3" />
                        <span>Documents:</span>
                    </div>
                    <DocumentBadges
                        documentUuids={data.document_uuids}
                        onStaleUuidsDetected={onStaleDocuments}
                    />
                </div>
            )}
        </>
    );
}

function StatusDot({ enabled }: { enabled: boolean }) {
    return (
        <div className="flex items-center gap-1.5">
            <Circle
                className={`h-2 w-2 ${
                    enabled
                        ? "fill-green-500 text-green-500"
                        : "fill-gray-400 text-gray-400"
                }`}
            />
            <span className="text-xs text-muted-foreground">
                {enabled ? "Enabled" : "Disabled"}
            </span>
        </div>
    );
}

// ─── Trigger curl example helper (rendered inside the dialog form) ────────

function TriggerCurlExample({ endpoint }: { endpoint: string }) {
    const [copied, setCopied] = useState(false);
    const curl = `curl -X POST "${endpoint}" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"phone_number": "+1234567890", "initial_context": {}}'`;

    return (
        <div className="grid gap-2">
            <p className="text-sm font-medium">API Endpoint</p>
            <p className="text-xs text-muted-foreground">
                Use this endpoint to trigger calls via API. Requires an API key in
                the X-API-Key header.{" "}
                <Link
                    href="/api-keys"
                    target="_blank"
                    className="text-primary underline hover:no-underline"
                >
                    Get your API key
                </Link>
            </p>
            <code className="text-xs break-all bg-muted px-2 py-1 rounded">
                {endpoint || "Generating..."}
            </code>
            <p className="text-sm font-medium pt-2">Example Request</p>
            <div className="relative">
                <pre className="text-xs bg-muted px-3 py-2 rounded overflow-x-auto whitespace-pre-wrap">
                    {curl}
                </pre>
                <Button
                    variant="outline"
                    size="icon"
                    className="absolute top-2 right-2"
                    onClick={async () => {
                        await navigator.clipboard.writeText(curl);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                    }}
                >
                    {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                </Button>
            </div>
        </div>
    );
}

// ─── GenericNode ──────────────────────────────────────────────────────────

interface GenericNodeProps extends NodeProps {
    data: FlowNodeData;
    type: string;
}

export const GenericNode = memo(({ data, selected, id, type }: GenericNodeProps) => {
    // Per-type metadata that StartCall/EndCall used to set via `additionalData`
    // (is_start / is_end). Pulled from the spec name here.
    const additionalData = useMemo<Record<string, boolean> | undefined>(() => {
        const out: Record<string, boolean> = {};
        if (type === "startCall") out.is_start = true;
        if (type === "endCall") out.is_end = true;
        return Object.keys(out).length > 0 ? out : undefined;
    }, [type]);

    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({
        id,
        additionalData,
    });
    const { saveWorkflow, tools, documents, recordings } = useWorkflow();
    const { bySpecName } = useNodeSpecs();
    const spec = bySpecName.get(type);

    // ── Form state ─────────────────────────────────────────────────────
    const [values, setValues] = useState<Record<string, unknown>>(() =>
        spec ? seedValues(data, spec) : {},
    );

    // Re-seed once the spec arrives (initial fetch race).
    useEffect(() => {
        if (spec && Object.keys(values).length === 0) {
            setValues(seedValues(data, spec));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [spec]);

    // ── Trigger auto-UUID + canvas copy state ──────────────────────────
    const [triggerCopied, setTriggerCopied] = useState(false);
    const handleCopyTrigger = useCallback(async () => {
        const endpoint = buildTriggerEndpoint(data.trigger_path);
        if (!endpoint) return;
        await navigator.clipboard.writeText(endpoint);
        setTriggerCopied(true);
        setTimeout(() => setTriggerCopied(false), 2000);
    }, [data.trigger_path]);

    // For trigger nodes without a path yet, generate one and persist.
    useEffect(() => {
        if (type !== "trigger") return;
        if (data.trigger_path) return;
        const newPath = crypto.randomUUID();
        handleSaveNodeData({ ...data, trigger_path: newPath });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [type]);

    // ── Stale tool/document cleanup (was duplicated in Start/Agent) ─────
    const handleStaleTools = useCallback(
        async (staleUuids: string[]) => {
            const cleaned = (data.tool_uuids ?? []).filter(
                (u) => !staleUuids.includes(u),
            );
            handleSaveNodeData({
                ...data,
                tool_uuids: cleaned.length > 0 ? cleaned : undefined,
            });
            await saveWorkflow();
        },
        [data, handleSaveNodeData, saveWorkflow],
    );
    const handleStaleDocuments = useCallback(
        async (staleUuids: string[]) => {
            const cleaned = (data.document_uuids ?? []).filter(
                (u) => !staleUuids.includes(u),
            );
            handleSaveNodeData({
                ...data,
                document_uuids: cleaned.length > 0 ? cleaned : undefined,
            });
            await saveWorkflow();
        },
        [data, handleSaveNodeData, saveWorkflow],
    );

    // ── Dirty / save / open handlers ────────────────────────────────────
    const propertyNames = useMemo(
        () => spec?.properties.map((p) => p.name) ?? [],
        [spec],
    );

    const isDirty = useMemo(() => {
        const d = data as unknown as Record<string, unknown>;
        return propertyNames.some((n) => values[n] !== d[n]);
    }, [values, data, propertyNames]);

    const handleSave = async () => {
        if (!spec) return;
        handleSaveNodeData({
            ...data,
            ...(values as Partial<FlowNodeData>),
        });
        setOpen(false);
        await saveWorkflow();
    };

    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen && spec) setValues(seedValues(data, spec));
        setOpen(newOpen);
    };

    useEffect(() => {
        if (open && spec) setValues(seedValues(data, spec));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data, open]);

    // ── Render ──────────────────────────────────────────────────────────
    const styleVariant = STYLE_VARIANT_BY_SPEC[type];
    const handles = HANDLES_BY_SPEC[type] ?? { source: true, target: true };
    const Icon = spec ? resolveIcon(spec.icon) : Circle;
    const docUrl = DOC_URL_BY_SPEC[type];

    // Edit dialog title: "Edit {display_name}". Webhook keeps the original
    // "Edit Webhook" wording — display_name is "Webhook" so it works out.
    const dialogTitle = spec ? `Edit ${spec.display_name}` : "Edit Node";
    const fallbackTitle = spec?.display_name ?? "Node";

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title={data.name || fallbackTitle}
                icon={<Icon />}
                nodeType={styleVariant}
                hasSourceHandle={handles.source}
                hasTargetHandle={handles.target}
                onDoubleClick={() => setOpen(true)}
                nodeId={id}
            >
                {spec && (
                    <CanvasPreview
                        spec={spec}
                        data={data}
                        onCopyTrigger={handleCopyTrigger}
                        triggerCopied={triggerCopied}
                        onStaleTools={handleStaleTools}
                        onStaleDocuments={handleStaleDocuments}
                    />
                )}
            </NodeContent>

            <NodeToolbar isVisible={selected} position={Position.Right}>
                <div className="flex flex-col gap-1">
                    <Button onClick={() => setOpen(true)} variant="outline" size="icon">
                        <Edit />
                    </Button>
                    {/* Start nodes can't be deleted (workflow always needs one). */}
                    {type !== "startCall" && (
                        <Button
                            onClick={handleDeleteNode}
                            variant="outline"
                            size="icon"
                        >
                            <Trash2Icon />
                        </Button>
                    )}
                </div>
            </NodeToolbar>

            <NodeEditDialog
                open={open}
                onOpenChange={handleOpenChange}
                nodeData={data}
                title={dialogTitle}
                onSave={handleSave}
                isDirty={isDirty}
                documentationUrl={docUrl}
            >
                {open && spec && (
                    <div className="grid gap-4">
                        <NodeEditForm
                            spec={spec}
                            values={values}
                            onChange={setValues}
                            context={{
                                tools: tools ?? [],
                                documents: documents ?? [],
                                recordings: recordings ?? [],
                            }}
                        />
                        {type === "trigger" && (
                            <TriggerCurlExample
                                endpoint={buildTriggerEndpoint(data.trigger_path)}
                            />
                        )}
                    </div>
                )}
            </NodeEditDialog>
        </>
    );
});

GenericNode.displayName = "GenericNode";
