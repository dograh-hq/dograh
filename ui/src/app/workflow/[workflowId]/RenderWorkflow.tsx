import '@xyflow/react/dist/style.css';

import {
    Background,
    BackgroundVariant,
    Panel,
    ReactFlow,
} from "@xyflow/react";
import { BrushCleaning, Maximize2, Minus, Plus, Settings } from 'lucide-react';
import { useRouter } from 'next/navigation';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { createWorkflowDraftApiV1WorkflowWorkflowIdCreateDraftPost, getWorkflowVersionsApiV1WorkflowWorkflowIdVersionsGet, listDocumentsApiV1KnowledgeBaseDocumentsGet, listRecordingsApiV1WorkflowRecordingsGet, listToolsApiV1ToolsGet } from '@/client';
import type { DocumentResponseSchema, RecordingResponseSchema, ToolResponse } from '@/client/types.gen';
import { useNodeSpecs } from "@/components/flow/renderer";
import { FlowEdge, FlowNode, NodeType } from "@/components/flow/types";
import { HireExpertNudge } from "@/components/lead-forms/HireExpertNudge";
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useOnboarding } from '@/context/OnboardingContext';
import { WorkflowConfigurations } from '@/types/workflow-configurations';

import AddNodePanel from "../../../components/flow/AddNodePanel";
import CustomEdge from "../../../components/flow/edges/CustomEdge";
import { GenericNode } from "../../../components/flow/nodes/GenericNode";
import { PhoneCallDialog } from './components/PhoneCallDialog';
import { VersionHistoryPanel, WorkflowVersion } from './components/VersionHistoryPanel';
import type { WorkflowRuntimeNodeTransition } from './components/workflow-tester/types';
import { WorkflowEditorHeader } from "./components/WorkflowEditorHeader";
import { WorkflowTesterPanel } from './components/WorkflowTesterPanel';
import { WorkflowProvider } from "./contexts/WorkflowContext";
import { useWorkflowState } from "./hooks/useWorkflowState";
import { layoutNodes } from './utils/layoutNodes';

const edgeTypes = {
    custom: CustomEdge,
};

const VERSIONS_PAGE_SIZE = 10;

interface RenderWorkflowProps {
    initialWorkflowName: string;
    workflowId: number;
    workflowUuid?: string;
    initialTotalRuns?: number | null;
    openTesterOnLoad?: boolean;
    initialFlow?: {
        nodes: FlowNode[];
        edges: FlowEdge[];
        viewport: {
            x: number;
            y: number;
            zoom: number;
        };
    };
    initialTemplateContextVariables?: Record<string, string>;
    initialWorkflowConfigurations?: WorkflowConfigurations;
    initialVersionNumber?: number | null;
    initialVersionStatus?: string | null;
    user: { id: string; email?: string };
}

function RenderWorkflow({
    initialWorkflowName,
    workflowId,
    workflowUuid,
    initialTotalRuns,
    openTesterOnLoad = false,
    initialFlow,
    initialTemplateContextVariables,
    initialWorkflowConfigurations,
    initialVersionNumber,
    initialVersionStatus,
    user,
}: RenderWorkflowProps) {
    const router = useRouter();
    const { specs } = useNodeSpecs();
    const { hasCompletedAction } = useOnboarding();
    const [isPhoneCallDialogOpen, setIsPhoneCallDialogOpen] = useState(false);
    const [isVersionPanelOpen, setIsVersionPanelOpen] = useState(false);
    const [isTesterRailOpen, setIsTesterRailOpen] = useState(true);
    const [isTesterSheetOpen, setIsTesterSheetOpen] = useState(false);
    const [isDesktopViewport, setIsDesktopViewport] = useState(false);
    const [versions, setVersions] = useState<WorkflowVersion[]>([]);
    const [versionsLoading, setVersionsLoading] = useState(false);
    const [versionsLoadingMore, setVersionsLoadingMore] = useState(false);
    const [versionsHasMore, setVersionsHasMore] = useState(false);
    const [activeVersionId, setActiveVersionId] = useState<number | null>(null);
    const hasAutoOpenedTester = useRef(false);
    // Version info that updates immediately from the GET/save/publish responses.
    const [currentVersionNumber, setCurrentVersionNumber] = useState<number | null>(initialVersionNumber ?? null);
    const [currentVersionStatus, setCurrentVersionStatus] = useState<string | null>(initialVersionStatus ?? null);
    const versionsFetched = useRef(false);
    const [documents, setDocuments] = useState<DocumentResponseSchema[] | undefined>(undefined);
    const [tools, setTools] = useState<ToolResponse[] | undefined>(undefined);
    const [recordings, setRecordings] = useState<RecordingResponseSchema[]>([]);
    const [activeRuntimeNodeId, setActiveRuntimeNodeId] = useState<string | null>(null);

    const {
        rfInstance,
        nodes,
        edges,
        isAddNodePanelOpen,
        workflowName,
        isDirty,
        workflowValidationErrors,
        templateContextVariables,
        setNodes,
        setEdges,
        setIsDirty,
        setIsAddNodePanelOpen,
        handleNodeSelect,
        saveWorkflow,
        workflowConfigurations,
        saveWorkflowConfigurations,
        onConnect,
        onEdgesChange,
        onNodesChange,
        onDelete,
    } = useWorkflowState({
        initialWorkflowName,
        workflowId,
        initialFlow,
        initialTemplateContextVariables,
        initialWorkflowConfigurations,
        user,
    });

    // Single generic component for every node type. Seed with core node types
    // so the initial render is stable before specs load, then merge in any
    // spec-defined or already-present node types so plugin integrations like
    // Tuner render without extra React registrations.
    const nodeTypes = useMemo(() => {
        const typeNames = new Set<string>([
            ...Object.values(NodeType),
            ...specs.map((spec) => spec.name),
            ...nodes.map((node) => node.type),
            ...(initialFlow?.nodes ?? []).map((node) => node.type),
        ]);
        return Object.fromEntries(
            Array.from(typeNames).map((typeName) => [typeName, GenericNode]),
        );
    }, [initialFlow?.nodes, nodes, specs]);

    // Derive hasDraft from the current version status
    const hasDraft = currentVersionStatus === "draft";

    // Fetch the first page of workflow versions, optionally forcing a refresh.
    // Pagination keeps the panel snappy when a workflow has accumulated a long
    // history — `workflow_json` is shipped per row, so loading hundreds at once
    // is expensive on the wire.
    const fetchVersions = useCallback(async (force = false) => {
        if (versionsFetched.current && !force) return;
        setVersionsLoading(true);
        try {
            const response = await getWorkflowVersionsApiV1WorkflowWorkflowIdVersionsGet({
                path: { workflow_id: workflowId },
                query: { limit: VERSIONS_PAGE_SIZE, offset: 0 },
            });
            const data = response.data as WorkflowVersion[] | undefined;
            if (data) {
                setVersions(data);
                setVersionsHasMore(data.length === VERSIONS_PAGE_SIZE);
                // Set active version to draft if exists, else published.
                // Both live on the newest page so the first fetch always sees them.
                const current = data.find((v) => v.status === "draft") ?? data.find((v) => v.status === "published");
                if (current) {
                    setActiveVersionId(current.id);
                    setCurrentVersionNumber(current.version_number);
                    setCurrentVersionStatus(current.status);
                }
            }
            versionsFetched.current = true;
        } finally {
            setVersionsLoading(false);
        }
    }, [workflowId]);

    const handleLoadMoreVersions = useCallback(async () => {
        if (versionsLoadingMore || !versionsHasMore) return;
        setVersionsLoadingMore(true);
        try {
            const response = await getWorkflowVersionsApiV1WorkflowWorkflowIdVersionsGet({
                path: { workflow_id: workflowId },
                query: { limit: VERSIONS_PAGE_SIZE, offset: versions.length },
            });
            const data = response.data as WorkflowVersion[] | undefined;
            if (data) {
                setVersions((prev) => [...prev, ...data]);
                setVersionsHasMore(data.length === VERSIONS_PAGE_SIZE);
            }
        } finally {
            setVersionsLoadingMore(false);
        }
    }, [workflowId, versions.length, versionsLoadingMore, versionsHasMore]);

    const handleOpenVersionPanel = useCallback(() => {
        setIsVersionPanelOpen(true);
        fetchVersions();
    }, [fetchVersions]);

    const handleSelectVersion = useCallback((version: WorkflowVersion) => {
        setActiveVersionId(version.id);
        const wfJson = version.workflow_json;
        const flowNodes = (wfJson.nodes ?? []) as FlowNode[];
        const flowEdges = (wfJson.edges ?? []) as FlowEdge[];

        // Update the Zustand store directly instead of rfInstance.current.setNodes().
        // This keeps data flow unidirectional (store → props → ReactFlow) and avoids
        // xyflow's d3 event handlers interfering with React's event delegation.
        // The key={activeVersionId} on <ReactFlow> forces a clean remount.
        setNodes(flowNodes);
        setEdges(flowEdges);
        // Never mark dirty when switching versions — historical versions are
        // read-only, and loading the draft is restoring the saved state.
        setIsDirty(false);
        setIsVersionPanelOpen(false);
    }, [setNodes, setEdges, setIsDirty]);

    // Determine if we are viewing a historical (non-current) version.
    // The "current" version is the draft if one exists, otherwise the published version.
    // Anything else (archived, or published while a draft exists) is historical.
    const isViewingHistoricalVersion = useMemo(() => {
        if (!activeVersionId || versions.length === 0) return false;
        const activeVersion = versions.find((v) => v.id === activeVersionId);
        if (!activeVersion) return false;
        if (activeVersion.status === "draft") return false;
        if (activeVersion.status === "published" && !hasDraft) return false;
        return true;
    }, [activeVersionId, versions, hasDraft]);

    useEffect(() => {
        if (!isViewingHistoricalVersion) {
            return;
        }
        setActiveRuntimeNodeId(null);
    }, [isViewingHistoricalVersion]);

    // Return to the draft version, creating one from published if needed
    const handleBackToDraft = useCallback(async () => {
        const existingDraft = versions.find((v) => v.status === "draft");
        if (existingDraft) {
            handleSelectVersion(existingDraft);
            return;
        }

        // No draft exists — ask the backend to create one from published
        const response = await createWorkflowDraftApiV1WorkflowWorkflowIdCreateDraftPost({
            path: { workflow_id: workflowId },
        });
        const draft = response.data;
        if (draft) {
            setCurrentVersionNumber(draft.version_number);
            setCurrentVersionStatus(draft.status);
            // Load draft nodes/edges via the Zustand store (same approach as handleSelectVersion)
            const flowNodes = (draft.workflow_json?.nodes ?? []) as FlowNode[];
            const flowEdges = (draft.workflow_json?.edges ?? []) as FlowEdge[];
            setNodes(flowNodes);
            setEdges(flowEdges);
            setActiveVersionId(draft.id);
            setIsDirty(false);
            // Refresh the version list so the new draft appears
            fetchVersions(true);
        }
    }, [versions, handleSelectVersion, workflowId, setNodes, setEdges, setIsDirty, fetchVersions]);

    // After a successful publish, refresh the version list and update status
    const handlePublished = useCallback(() => {
        setCurrentVersionStatus("published");
        fetchVersions(true);
    }, [fetchVersions]);

    // Compute version label for the header.
    // Uses currentVersionNumber/Status which update immediately from save responses,
    // falling back to the versions list for history navigation.
    const activeVersionLabel = useMemo(() => {
        // When viewing a version from the history panel, use the versions list
        if (activeVersionId && versions.length > 0) {
            const v = versions.find((ver) => ver.id === activeVersionId);
            if (v) {
                const statusSuffix = v.status === "draft" ? " (Draft)" : v.status === "published" ? " (Published)" : "";
                return `v${v.version_number}${statusSuffix}`;
            }
        }
        // Otherwise use the immediately-available version info from save responses
        if (currentVersionNumber != null) {
            const statusSuffix = currentVersionStatus === "draft" ? " (Draft)" : currentVersionStatus === "published" ? " (Published)" : "";
            return `v${currentVersionNumber}${statusSuffix}`;
        }
        return undefined;
    }, [activeVersionId, versions, currentVersionNumber, currentVersionStatus]);

    const testerDisabledReason = useMemo(() => {
        if (isViewingHistoricalVersion) {
            return "Return to the draft before starting a new test session.";
        }
        if (isDirty) {
            return "Save the latest draft before testing so the session uses the workflow you are looking at.";
        }
        if (workflowValidationErrors.length > 0) {
            return "Resolve the current validation errors before starting another test.";
        }
        return null;
    }, [isDirty, isViewingHistoricalVersion, workflowValidationErrors.length]);

    const handleOpenTester = useCallback(() => {
        if (window.innerWidth >= 1280) {
            setIsTesterRailOpen(true);
            return;
        }
        setIsTesterSheetOpen(true);
    }, []);

    const shouldShowWebCallOnboarding = useMemo(() => {
        return (initialTotalRuns ?? 0) === 0 && !hasCompletedAction('web_call_started');
    }, [hasCompletedAction, initialTotalRuns]);

    useEffect(() => {
        const syncViewport = () => {
            setIsDesktopViewport(window.innerWidth >= 1280);
        };

        syncViewport();
        window.addEventListener('resize', syncViewport);
        return () => window.removeEventListener('resize', syncViewport);
    }, []);

    useEffect(() => {
        if (hasAutoOpenedTester.current || !openTesterOnLoad || !shouldShowWebCallOnboarding || testerDisabledReason) {
            return;
        }

        handleOpenTester();
        hasAutoOpenedTester.current = true;
    }, [handleOpenTester, openTesterOnLoad, shouldShowWebCallOnboarding, testerDisabledReason]);

    // Fetch documents, tools, and recordings once for the entire workflow
    useEffect(() => {
        const fetchData = async () => {
            try {
                // Fetch documents
                const documentsResponse = await listDocumentsApiV1KnowledgeBaseDocumentsGet({
                    query: { limit: 100 },
                });
                if (documentsResponse.data) {
                    setDocuments(documentsResponse.data.documents);
                }

                // Fetch tools
                const toolsResponse = await listToolsApiV1ToolsGet({});
                if (toolsResponse.data) {
                    setTools(toolsResponse.data);
                }

                // Fetch org-level recordings
                try {
                    const recordingsResponse = await listRecordingsApiV1WorkflowRecordingsGet({
                        query: {},
                    });
                    if (recordingsResponse.data) {
                        setRecordings(recordingsResponse.data.recordings);
                    }
                } catch {
                    // Recordings API may not be available yet; silently ignore
                }
            } catch (error) {
                console.error('Failed to fetch documents and tools:', error);
            }
        };

        fetchData();
    }, [workflowId]);

    // Memoize defaultEdgeOptions to prevent unnecessary re-renders
    const defaultEdgeOptions = useMemo(() => ({
        animated: true,
        type: "custom"
    }), []);

    const displayNodes = useMemo(
        () =>
            nodes.map((node) =>
                node.id === activeRuntimeNodeId
                    ? {
                          ...node,
                          data: {
                              ...node.data,
                              runtime_active: true,
                          },
                      }
                    : node,
            ),
        [activeRuntimeNodeId, nodes],
    );

    const handleRuntimeNodeTransition = useCallback(
        (transition: WorkflowRuntimeNodeTransition) => {
            const nodeId = transition.nodeId;
            const instance = rfInstance.current;
            if (!nodeId || !instance) {
                return;
            }

            setActiveRuntimeNodeId(nodeId);

            if (!instance.viewportInitialized) {
                return;
            }

            void instance.fitView({
                nodes: [{ id: nodeId }],
                duration: 350,
                padding: 0.45,
                maxZoom: 0.9,
            });
        },
        [rfInstance],
    );

    // Guard saveWorkflow so it's a no-op when viewing a historical version.
    // This is the single safety net that covers every save path: header button,
    // Cmd+S, node edit dialogs, stale doc/tool cleanup, etc.
    // Uses the save response to immediately update version label and hasDraft.
    const guardedSaveWorkflow = useCallback(async (updateWorkflowDefinition?: boolean) => {
        if (isViewingHistoricalVersion) return;
        const result = await saveWorkflow(updateWorkflowDefinition);
        if (result) {
            // If the versions list has been fetched (user interacted with versioning
            // or published), refresh it so that activeVersionId points to the correct
            // version.  This is critical when a save creates a new draft from a
            // published version: without refreshing, activeVersionId would still
            // point to the old published version, causing isViewingHistoricalVersion
            // to incorrectly return true and lock the editor into read-only mode.
            if (versionsFetched.current) {
                await fetchVersions(true);
            } else {
                if (result.versionNumber != null) setCurrentVersionNumber(result.versionNumber);
                if (result.versionStatus) setCurrentVersionStatus(result.versionStatus);
            }
        }
    }, [saveWorkflow, isViewingHistoricalVersion, fetchVersions]);

    const renameWorkflow = useCallback(async (newName: string) => {
        // The header doesn't render the pencil until the page has mounted with
        // initial data, so workflowConfigurations is non-null by the time this
        // runs. Throw rather than silently sending fallback workflow configurations,
        // which would overwrite the saved server-side config.
        if (!workflowConfigurations) {
            throw new Error("Workflow configurations not loaded");
        }
        await saveWorkflowConfigurations(workflowConfigurations, newName);
    }, [saveWorkflowConfigurations, workflowConfigurations]);

    const updateTool = useCallback(
        (toolUuid: string, updater: (tool: ToolResponse) => ToolResponse) => {
            setTools((prev) =>
                prev?.map((tool) =>
                    tool.tool_uuid === toolUuid ? updater(tool) : tool,
                ),
            );
        },
        [],
    );

    // Memoize the context value to prevent unnecessary re-renders
    const workflowContextValue = useMemo(() => ({
        saveWorkflow: guardedSaveWorkflow,
        documents,
        tools,
        updateTool,
        recordings,
        readOnly: isViewingHistoricalVersion,
    }), [
        guardedSaveWorkflow,
        documents,
        tools,
        updateTool,
        recordings,
        isViewingHistoricalVersion,
    ]);

    return (
        <WorkflowProvider value={workflowContextValue}>
            <div className="flex flex-col h-screen min-w-fit">
                <HireExpertNudge workflowId={workflowId} />
                {/* New Workflow Editor Header */}
                <WorkflowEditorHeader
                    workflowName={workflowName}
                    isDirty={isDirty}
                    workflowValidationErrors={workflowValidationErrors}
                    rfInstance={rfInstance}
                    workflowId={workflowId}
                    workflowUuid={workflowUuid}
                    saveWorkflow={guardedSaveWorkflow}
                    user={user}
                    onPhoneCallClick={() => setIsPhoneCallDialogOpen(true)}
                    onTestAgentClick={handleOpenTester}
                    onHistoryClick={handleOpenVersionPanel}
                    activeVersionLabel={activeVersionLabel}
                    isViewingHistoricalVersion={isViewingHistoricalVersion}
                    onBackToDraft={handleBackToDraft}
                    hasDraft={hasDraft}
                    onPublished={handlePublished}
                    renameWorkflow={renameWorkflow}
                />

                {/* Workflow Canvas */}
                <div className="flex-1 min-h-0">
                    <div className="flex h-full min-w-0">
                        <div className="relative min-w-0 flex-1">
                            <ReactFlow
                                key={activeVersionId ?? 'current'}
                                nodes={displayNodes}
                                edges={edges}
                                onNodesChange={onNodesChange}
                                onEdgesChange={onEdgesChange}
                                onDelete={onDelete}
                                nodeTypes={nodeTypes}
                                edgeTypes={edgeTypes}
                                onConnect={isViewingHistoricalVersion ? undefined : onConnect}
                                minZoom={0.4}
                                onInit={(instance) => {
                                    rfInstance.current = instance;
                                    // Center the workflow on load
                                    setTimeout(() => {
                                        instance.fitView({ padding: 0.2, duration: 200, maxZoom: 0.75 });
                                    }, 0);
                                }}
                                defaultEdgeOptions={defaultEdgeOptions}
                                defaultViewport={initialFlow?.viewport}
                                nodesDraggable={!isViewingHistoricalVersion}
                                nodesConnectable={!isViewingHistoricalVersion}
                                edgesReconnectable={!isViewingHistoricalVersion}
                                zoomOnDoubleClick={false}
                                deleteKeyCode={isViewingHistoricalVersion ? null : "Backspace"}
                            >
                                <Background
                                    variant={BackgroundVariant.Dots}
                                    gap={16}
                                    size={1}
                                    color="#94a3b8"
                                />

                                {/* Top-right controls - vertical layout (hidden when viewing history) */}
                                {!isViewingHistoricalVersion && (
                                    <Panel position="top-right">
                                        <TooltipProvider>
                                            <div className="flex flex-col gap-2">
                                                <Tooltip>
                                                    <TooltipTrigger asChild>
                                                        <Button
                                                            variant="default"
                                                            size="icon"
                                                            onClick={() => setIsAddNodePanelOpen(true)}
                                                            className="shadow-md hover:shadow-lg"
                                                        >
                                                            <Plus className="h-4 w-4" />
                                                        </Button>
                                                    </TooltipTrigger>
                                                    <TooltipContent side="left">
                                                        <p>Add node</p>
                                                    </TooltipContent>
                                                </Tooltip>

                                                <Tooltip>
                                                    <TooltipTrigger asChild>
                                                        <Button
                                                            variant="outline"
                                                            size="icon"
                                                            onClick={() => router.push(`/workflow/${workflowId}/settings`)}
                                                            className="bg-white shadow-sm hover:shadow-md"
                                                        >
                                                            <Settings className="h-4 w-4" />
                                                        </Button>
                                                    </TooltipTrigger>
                                                    <TooltipContent side="left">
                                                        <p>Workflow settings</p>
                                                    </TooltipContent>
                                                </Tooltip>
                                            </div>
                                        </TooltipProvider>
                                    </Panel>
                                )}
                            </ReactFlow>

                            {/* Bottom-left controls - horizontal layout with custom buttons */}
                            <div className="absolute bottom-12 left-8 z-10 flex gap-2">
                                <TooltipProvider>
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Button
                                                variant="outline"
                                                size="icon"
                                                onClick={() => rfInstance.current?.zoomIn()}
                                                className="bg-white shadow-sm hover:shadow-md h-8 w-8"
                                            >
                                                <Plus className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="top">
                                            <p>Zoom in</p>
                                        </TooltipContent>
                                    </Tooltip>

                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Button
                                                variant="outline"
                                                size="icon"
                                                onClick={() => rfInstance.current?.zoomOut()}
                                                className="bg-white shadow-sm hover:shadow-md h-8 w-8"
                                            >
                                                <Minus className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="top">
                                            <p>Zoom out</p>
                                        </TooltipContent>
                                    </Tooltip>

                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Button
                                                variant="outline"
                                                size="icon"
                                                onClick={() => rfInstance.current?.fitView()}
                                                className="bg-white shadow-sm hover:shadow-md h-8 w-8"
                                            >
                                                <Maximize2 className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="top">
                                            <p>Fit view</p>
                                        </TooltipContent>
                                    </Tooltip>

                                    {!isViewingHistoricalVersion && (
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <Button
                                                    variant="outline"
                                                    size="icon"
                                                    onClick={() => {
                                                        setNodes(layoutNodes(nodes, edges, 'TB', rfInstance));
                                                        setIsDirty(true);
                                                    }}
                                                    className="bg-white shadow-sm hover:shadow-md h-8 w-8"
                                                >
                                                    <BrushCleaning className="h-4 w-4" />
                                                </Button>
                                            </TooltipTrigger>
                                            <TooltipContent side="top">
                                                <p>Tidy Up</p>
                                            </TooltipContent>
                                        </Tooltip>
                                    )}
                                </TooltipProvider>
                            </div>
                        </div>

                        {isTesterRailOpen && (
                            <aside className="hidden h-full w-[400px] shrink-0 border-l border-border xl:block">
                                <WorkflowTesterPanel
                                    workflowId={workflowId}
                                    initialContextVariables={templateContextVariables}
                                    disabled={testerDisabledReason !== null}
                                    disabledReason={testerDisabledReason}
                                    showWebCallOnboarding={shouldShowWebCallOnboarding}
                                    isVisible={isDesktopViewport}
                                    onClose={() => setIsTesterRailOpen(false)}
                                    onRuntimeNodeTransition={handleRuntimeNodeTransition}
                                />
                            </aside>
                        )}
                    </div>

                    <Sheet open={isTesterSheetOpen} onOpenChange={setIsTesterSheetOpen}>
                        <SheetContent side="right" className="w-full max-w-none p-0 sm:max-w-xl xl:hidden">
                            <WorkflowTesterPanel
                                workflowId={workflowId}
                                initialContextVariables={templateContextVariables}
                                disabled={testerDisabledReason !== null}
                                disabledReason={testerDisabledReason}
                                showWebCallOnboarding={shouldShowWebCallOnboarding}
                                isVisible={isTesterSheetOpen}
                                onRuntimeNodeTransition={handleRuntimeNodeTransition}
                            />
                        </SheetContent>
                    </Sheet>
                </div>

                <AddNodePanel
                    isOpen={isAddNodePanelOpen}
                    onNodeSelect={handleNodeSelect}
                    onClose={() => setIsAddNodePanelOpen(false)}
                    nodes={nodes}
                />

                <VersionHistoryPanel
                    isOpen={isVersionPanelOpen}
                    onClose={() => setIsVersionPanelOpen(false)}
                    versions={versions}
                    loading={versionsLoading}
                    activeVersionId={activeVersionId}
                    onSelectVersion={handleSelectVersion}
                    hasMore={versionsHasMore}
                    loadingMore={versionsLoadingMore}
                    onLoadMore={handleLoadMoreVersions}
                />

                <PhoneCallDialog
                    open={isPhoneCallDialogOpen}
                    onOpenChange={setIsPhoneCallDialogOpen}
                    workflowId={workflowId}
                    user={user}
                />
            </div>
        </WorkflowProvider>
    );
}

// Memoize the component to prevent unnecessary re-renders when parent re-renders
export default React.memo(RenderWorkflow, (prevProps, nextProps) => {
    // Only re-render if these specific props change
    return (
        prevProps.workflowId === nextProps.workflowId &&
        prevProps.initialWorkflowName === nextProps.initialWorkflowName &&
        prevProps.user.id === nextProps.user.id
        // Note: We intentionally don't compare initialFlow, initialTemplateContextVariables,
        // or initialWorkflowConfigurations because they're only used for initialization
    );
});
