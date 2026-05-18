import {
    applyEdgeChanges,
    applyNodeChanges,
    OnConnect,
    OnEdgesChange,
    OnNodesChange,
    ReactFlowInstance,
} from "@xyflow/react";
import { EdgeChange, NodeChange } from "@xyflow/system";
import { useRouter } from "next/navigation";
import posthog from "posthog-js";
import { useCallback, useEffect, useRef } from "react";

import { useWorkflowStore } from "@/app/workflow/[workflowId]/stores/workflowStore";
import {
    createWorkflowRunApiV1WorkflowWorkflowIdRunsPost,
    updateWorkflowApiV1WorkflowWorkflowIdPut,
    validateWorkflowApiV1WorkflowWorkflowIdValidatePost
} from "@/client";
import { NodeSpec, WorkflowError } from "@/client/types.gen";
import { useNodeSpecs } from "@/components/flow/renderer";
import { FlowEdge, FlowNode, FlowNodeData, NodeType } from "@/components/flow/types";
import { PostHogEvent } from "@/constants/posthog-events";
import logger from '@/lib/logger';
import { getNextNodeId, getRandomId } from "@/lib/utils";
import { DEFAULT_WORKFLOW_CONFIGURATIONS, WorkflowConfigurations } from "@/types/workflow-configurations";

// Pull a WorkflowError[] out of any validate-shaped payload — works whether
// the body is the raw `{ is_valid, errors }` (validate success-with-errors)
// or wrapped as `{ detail: { is_valid, errors } }` (HTTPException body for
// validate's 422 and save's 409). Returns [] for any other shape so callers
// can tell "no structured errors in this response" from "valid".
function extractWorkflowErrors(payload: unknown): WorkflowError[] {
    if (!payload || typeof payload !== "object") return [];
    const p = payload as {
        is_valid?: boolean;
        errors?: WorkflowError[];
        detail?: { is_valid?: boolean; errors?: WorkflowError[] } | string;
    };
    if (p.is_valid === false && p.errors) return p.errors;
    if (typeof p.detail === "object" && p.detail?.errors) return p.detail.errors;
    return [];
}

// Build initial node data from spec defaults. Replaces the per-type
// hardcoded `getNewNode` switch — adding a new node type is now zero
// frontend code: declare the spec on the backend and the defaults flow
// through here.
function buildDataFromSpec(spec: NodeSpec): Record<string, unknown> {
    const data: Record<string, unknown> = {};
    for (const prop of spec.properties) {
        if (prop.default !== undefined && prop.default !== null) {
            data[prop.name] = prop.default;
        }
    }
    return data;
}

function buildNewNode(
    type: string,
    position: { x: number; y: number },
    existingNodes: FlowNode[],
    spec: NodeSpec,
): FlowNode {
    const data = buildDataFromSpec(spec) as Partial<FlowNodeData> & Record<string, unknown>;
    if (type === NodeType.START_CALL) data.is_start = true;
    if (type === NodeType.END_CALL) data.is_end = true;
    return {
        id: getNextNodeId(existingNodes),
        type,
        position,
        data: data as unknown as FlowNode["data"],
    };
}

// Look up the spec default for `allow_interrupt`. Used as a load-time
// fallback for older saved workflows whose nodes lack the field.
function specAllowInterrupt(
    type: string,
    bySpecName: Map<string, NodeSpec>,
): boolean | undefined {
    const prop = bySpecName.get(type)?.properties.find((p) => p.name === "allow_interrupt");
    return prop?.default as boolean | undefined;
}

interface UseWorkflowStateProps {
    initialWorkflowName: string;
    workflowId: number;
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
    user: { id: string; email?: string } | null;
}

export const useWorkflowState = ({
    initialWorkflowName,
    workflowId,
    initialFlow,
    initialTemplateContextVariables,
    initialWorkflowConfigurations,
    user,
}: UseWorkflowStateProps) => {
    const router = useRouter();
    const rfInstance = useRef<ReactFlowInstance<FlowNode, FlowEdge> | null>(null);

    // Spec catalog. Workflow init waits on this to populate defaults; node
    // creation looks up per-type schemas through it.
    const { bySpecName, loading: specsLoading } = useNodeSpecs();

    // Get state and actions from the store
    const {
        nodes,
        edges,
        workflowName,
        isDirty,
        isAddNodePanelOpen,
        workflowValidationErrors,
        templateContextVariables,
        workflowConfigurations,
        initializeWorkflow,
        setNodes,
        setEdges,
        setWorkflowName,
        setIsDirty,
        setIsAddNodePanelOpen,
        setWorkflowValidationErrors,
        setTemplateContextVariables,
        setWorkflowConfigurations,
        setDictionary,
        dictionary,
        clearValidationErrors,
        markNodeAsInvalid,
        markEdgeAsInvalid,
        setRfInstance,
    } = useWorkflowStore();

    // Get undo/redo functions from the store
    const undo = useWorkflowStore((state) => state.undo);
    const redo = useWorkflowStore((state) => state.redo);
    const canUndo = useWorkflowStore((state) => state.canUndo());
    const canRedo = useWorkflowStore((state) => state.canRedo());

    // Initialize workflow on mount. Waits for the spec catalog so defaults
    // (allow_interrupt, prompt placeholders, etc.) come from one source.
    useEffect(() => {
        if (specsLoading) return;

        const startSpec = bySpecName.get(NodeType.START_CALL);
        const fallbackStartNodes: FlowNode[] = startSpec
            ? [buildNewNode(NodeType.START_CALL, { x: 200, y: 200 }, [], startSpec)]
            : [];

        const initialNodes = initialFlow?.nodes?.length
            ? initialFlow.nodes.map((node) => {
                const fallbackAllowInterrupt = specAllowInterrupt(node.type, bySpecName) ?? false;
                return {
                    ...node,
                    data: {
                        ...node.data,
                        invalid: false,
                        allow_interrupt:
                            node.data.allow_interrupt !== undefined
                                ? node.data.allow_interrupt
                                : fallbackAllowInterrupt,
                    },
                };
            })
            : fallbackStartNodes;

        initializeWorkflow(
            workflowId,
            initialWorkflowName,
            initialNodes,
            initialFlow?.edges ?? [],
            initialTemplateContextVariables,
            initialWorkflowConfigurations,
            initialWorkflowConfigurations?.dictionary ?? ''
        );
    }, [workflowId, initialWorkflowName, initialFlow?.nodes, initialFlow?.edges, initialTemplateContextVariables, initialWorkflowConfigurations, initializeWorkflow, specsLoading, bySpecName]);

    // Set up keyboard shortcuts for undo/redo
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            // Check if we're in an input field
            const target = e.target as HTMLElement;
            if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
                return;
            }

            // Undo: Cmd/Ctrl + Z
            if ((e.metaKey || e.ctrlKey) && e.key === 'z' && !e.shiftKey) {
                e.preventDefault();
                if (canUndo) {
                    undo();
                }
            }
            // Redo: Cmd/Ctrl + Shift + Z or Cmd/Ctrl + Y
            else if (
                ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'z') ||
                ((e.metaKey || e.ctrlKey) && e.key === 'y')
            ) {
                e.preventDefault();
                if (canRedo) {
                    redo();
                }
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [undo, redo, canUndo, canRedo]);

    const handleNodeSelect = useCallback((nodeType: string) => {
        if (!rfInstance.current) return;

        const position = rfInstance.current.screenToFlowPosition({
            x: window.innerWidth / 2,
            y: window.innerHeight / 2,
        });

        const spec = bySpecName.get(nodeType);
        if (!spec) {
            logger.warn({ nodeType }, "No spec registered for node type — cannot add");
            return;
        }
        const newNode = {
            ...buildNewNode(nodeType, position, nodes, spec),
            selected: true, // Mark the new node as selected
        };

        // Deselect all existing nodes before adding the new one
        const currentNodes = rfInstance.current.getNodes();
        const deselectedNodes = currentNodes.map(node => ({ ...node, selected: false }));
        rfInstance.current.setNodes(deselectedNodes);

        // Use addNodes from ReactFlow instance
        rfInstance.current.addNodes([newNode]);
        posthog.capture(PostHogEvent.WORKFLOW_NODE_ADDED, {
            node_type: nodeType,
            workflow_id: workflowId,
        });
        setIsAddNodePanelOpen(false);
    }, [nodes, setIsAddNodePanelOpen, workflowId, bySpecName]);

    const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setWorkflowName(e.target.value);
        setIsDirty(true);
    };

    // Replace the canvas's validation state with `errors`. Always clears any
    // prior invalid markers first, so passing [] is the "workflow is now
    // valid" path.
    const applyWorkflowErrors = useCallback(
        (errors: WorkflowError[]) => {
            clearValidationErrors();
            errors.forEach((error) => {
                if (error.kind === "node" && error.id) {
                    markNodeAsInvalid(error.id, error.message);
                } else if (error.kind === "edge" && error.id) {
                    markEdgeAsInvalid(error.id, error.message);
                }
            });
            setWorkflowValidationErrors(errors);
        },
        [
            clearValidationErrors,
            markNodeAsInvalid,
            markEdgeAsInvalid,
            setWorkflowValidationErrors,
        ],
    );

    // Validate workflow function
    const validateWorkflow = useCallback(async () => {
        if (!user?.id) return;
        try {
            const response = await validateWorkflowApiV1WorkflowWorkflowIdValidatePost({
                path: {
                    workflow_id: workflowId,
                },
            });
            // 422 surfaces under response.error, 200 with is_valid=true under
            // response.data. extractWorkflowErrors normalises both — empty
            // list means "valid" and clears any stale highlights.
            applyWorkflowErrors(
                extractWorkflowErrors(response.error ?? response.data),
            );
        } catch (error: unknown) {
            logger.error(`Unexpected validation error: ${error}`);
        }
    }, [workflowId, user, applyWorkflowErrors]);

    // Save workflow function. Returns version info from the API response.
    const saveWorkflow = useCallback(async (updateWorkflowDefinition: boolean = true): Promise<{ versionNumber?: number; versionStatus?: string } | undefined> => {
        if (!user?.id || !rfInstance.current) return;
        // Read nodes/edges from the Zustand store (synchronously up-to-date)
        // and viewport from the ReactFlow instance to build the flow object.
        // This avoids a race condition where rfInstance.toObject() may return
        // stale node data if React hasn't re-rendered yet after a store update.
        const { nodes: currentNodes, edges: currentEdges } = useWorkflowStore.getState();
        const viewport = rfInstance.current.getViewport();
        const flow = { nodes: currentNodes, edges: currentEdges, viewport };
        let result: { versionNumber?: number; versionStatus?: string } | undefined;
        let saveSucceeded = false;
        try {
            const response = await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: {
                    workflow_id: workflowId,
                },
                body: {
                    name: workflowName,
                    workflow_definition: updateWorkflowDefinition ? flow : null,
                },
            });
            if (response.error) {
                // Backend rejected the save (e.g. 409 trigger-path conflict).
                // When it carries structured WorkflowError items, reuse the
                // validate pipeline so the offending node/edge gets
                // highlighted in-canvas. We only apply when there are
                // structured errors — a non-structured failure (network,
                // 500) shouldn't wipe the existing validation state.
                const workflowErrors = extractWorkflowErrors(response.error);
                if (workflowErrors.length > 0) {
                    applyWorkflowErrors(workflowErrors);
                }
                logger.error(`Error saving workflow: ${JSON.stringify(response.error)}`);
            } else {
                setIsDirty(false);
                if (response.data) {
                    // Reload server state into the canvas — the backend may
                    // have mutated the definition (e.g. minted a missing
                    // trigger_path) and is the source of truth post-save.
                    // Passing no `changes` arg skips history/dirty tracking.
                    const wf = response.data.workflow_definition as
                        | { nodes?: FlowNode[]; edges?: FlowEdge[] }
                        | undefined;
                    if (wf?.nodes) setNodes(wf.nodes);
                    if (wf?.edges) setEdges(wf.edges);
                    result = {
                        versionNumber: response.data.version_number ?? undefined,
                        versionStatus: response.data.version_status ?? undefined,
                    };
                    saveSucceeded = true;
                }
            }
        } catch (error) {
            logger.error(`Error saving workflow: ${error}`);
        }

        // Only run validate after a successful save — when save failed we've
        // already populated the validation state from the error response and
        // re-running validate would clear those errors (validate reads the
        // unchanged DB state, which won't surface the user's pending issue).
        if (saveSucceeded) {
            await validateWorkflow();
        }
        return result;
    }, [
        workflowId,
        workflowName,
        setIsDirty,
        setNodes,
        setEdges,
        user,
        validateWorkflow,
        applyWorkflowErrors,
    ]);

    // Set up keyboard shortcut for save (Cmd/Ctrl + S)
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault();
                if (useWorkflowStore.getState().isDirty) {
                    saveWorkflow();
                }
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [saveWorkflow]);

    const onConnect: OnConnect = useCallback((connection) => {
        if (!rfInstance.current) return;

        // Use addEdges from ReactFlow instance
        rfInstance.current.addEdges([{
            ...connection,
            id: `${connection.source}-${connection.target}`,
            data: {
                label: '',
                condition: ''
            }
        }]);
    }, []);

    const onEdgesChange: OnEdgesChange = useCallback(
        (changes) => {
            const currentEdges = useWorkflowStore.getState().edges;
            const newEdges = applyEdgeChanges(changes, currentEdges) as FlowEdge[];
            // Cast changes to FlowEdge type - safe because setEdges only uses the type field
            // to determine history tracking, not the actual item data
            setEdges(newEdges, changes as EdgeChange<FlowEdge>[]);
        },
        [setEdges],
    );

    const onNodesChange: OnNodesChange = useCallback(
        (changes) => {
            const currentNodes = useWorkflowStore.getState().nodes;
            const newNodes = applyNodeChanges(changes, currentNodes) as FlowNode[];
            // Cast changes to FlowNode type - safe because setNodes only uses the type field
            // to determine history tracking, not the actual item data
            setNodes(newNodes, changes as NodeChange<FlowNode>[]);
        },
        [setNodes],
    );

    const onRun = async (mode: string) => {
        if (!user?.id) return;
        const workflowRunName = `WR-${getRandomId()}`;
        const response = await createWorkflowRunApiV1WorkflowWorkflowIdRunsPost({
            path: {
                workflow_id: workflowId,
            },
            body: {
                mode,
                name: workflowRunName
            },
        });
        router.push(`/workflow/${workflowId}/run/${response.data?.id}`);
    };

    // Save template context variables
    const saveTemplateContextVariables = useCallback(async (variables: Record<string, string>) => {
        if (!user?.id) return;
        try {
            await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: {
                    workflow_id: workflowId,
                },
                body: {
                    name: workflowName,
                    workflow_definition: null,
                    template_context_variables: variables,
                },
            });
            setTemplateContextVariables(variables);
            logger.info('Template context variables saved successfully');
        } catch (error) {
            logger.error(`Error saving template context variables: ${error}`);
            throw error;
        }
    }, [workflowId, workflowName, user, setTemplateContextVariables]);

    // Save workflow configurations
    const saveWorkflowConfigurations = useCallback(async (configurations: WorkflowConfigurations, newWorkflowName: string) => {
        if (!user?.id) return;
        // Preserve the current dictionary when saving other configurations
        const currentDictionary = useWorkflowStore.getState().dictionary;
        const configurationsWithDictionary: WorkflowConfigurations = { ...configurations, dictionary: currentDictionary };
        try {
            const response = await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: {
                    workflow_id: workflowId,
                },
                body: {
                    name: newWorkflowName,
                    workflow_definition: null,
                    workflow_configurations: configurationsWithDictionary as Record<string, unknown>,
                },
            });

            if (response.error) {
                const detail = (response.error as { detail?: unknown }).detail;
                let msg = 'Failed to save workflow configurations';
                if (typeof detail === 'string') {
                    msg = detail;
                } else if (Array.isArray(detail)) {
                    msg = detail
                        .map((e: { model?: string; message?: string; msg?: string }) =>
                            e.model && e.message ? `${e.model}: ${e.message}` : (e.msg || JSON.stringify(e))
                        )
                        .join('\n');
                }
                throw new Error(msg);
            }

            setWorkflowConfigurations(configurationsWithDictionary);
            // Set name directly in the store to avoid setWorkflowName which marks isDirty: true
            useWorkflowStore.setState({ workflowName: newWorkflowName });
            logger.info('Workflow configurations saved successfully');
        } catch (error) {
            logger.error(`Error saving workflow configurations: ${error}`);
            throw error;
        }
    }, [workflowId, user, setWorkflowConfigurations]);

    // Save dictionary
    const saveDictionary = useCallback(async (newDictionary: string) => {
        if (!user) return;
        const currentConfigurations = useWorkflowStore.getState().workflowConfigurations ?? DEFAULT_WORKFLOW_CONFIGURATIONS;
        const updatedConfigurations: WorkflowConfigurations = { ...currentConfigurations, dictionary: newDictionary };
        try {
            await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: {
                    workflow_id: workflowId,
                },
                body: {
                    name: workflowName,
                    workflow_definition: null,
                    workflow_configurations: updatedConfigurations as Record<string, unknown>,
                },
            });
            setDictionary(newDictionary);
            setWorkflowConfigurations(updatedConfigurations);
        } catch (error) {
            logger.error(`Error saving dictionary: ${error}`);
            throw error;
        }
    }, [workflowId, workflowName, user, setDictionary, setWorkflowConfigurations]);

    // Update rfInstance when it changes
    useEffect(() => {
        if (rfInstance.current) {
            setRfInstance(rfInstance.current);
        }
    }, [setRfInstance]);

    // Validate workflow on mount
    useEffect(() => {
        validateWorkflow();
    }, [validateWorkflow]);

    return {
        rfInstance,
        nodes,
        edges,
        isAddNodePanelOpen,
        workflowName,
        isDirty,
        workflowValidationErrors,
        templateContextVariables,
        workflowConfigurations,
        dictionary,
        setNodes,
        setEdges,
        setIsDirty,
        setIsAddNodePanelOpen,
        handleNodeSelect,
        handleNameChange,
        saveWorkflow,
        onConnect,
        onEdgesChange,
        onNodesChange,
        onRun,
        saveTemplateContextVariables,
        saveWorkflowConfigurations,
        saveDictionary,
        // Export undo/redo state
        undo,
        redo,
        canUndo,
        canRedo,
    };
};
