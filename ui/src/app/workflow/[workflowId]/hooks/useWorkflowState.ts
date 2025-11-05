import {
    applyEdgeChanges,
    applyNodeChanges,
    OnConnect,
    OnEdgesChange,
    OnNodesChange,
    ReactFlowInstance,
    useEdgesState,
    useNodesState
} from "@xyflow/react";
import { addEdge } from "@xyflow/react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createWorkflowRunApiV1WorkflowWorkflowIdRunsPost,
    updateWorkflowApiV1WorkflowWorkflowIdPut,
    validateWorkflowApiV1WorkflowWorkflowIdValidatePost
} from "@/client";
import { WorkflowError } from "@/client/types.gen";
import { FlowEdge, FlowNode, NodeType } from "@/components/flow/types";
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { getRandomId } from "@/lib/utils";
import { DEFAULT_WORKFLOW_CONFIGURATIONS,WorkflowConfigurations } from "@/types/workflow-configurations";

export function getDefaultAllowInterrupt(type: string = NodeType.START_CALL): boolean {
    switch (type) {
        case NodeType.AGENT_NODE:
            return true; // Agents can be interrupted
        case NodeType.START_CALL:
        case NodeType.END_CALL:
            return false; // Start/End messages should not be interrupted
        default:
            return false;
    }
}

const defaultNodes: FlowNode[] = [
    {
        id: "1",
        type: NodeType.START_CALL,
        position: { x: 200, y: 200 },
        data: {
            prompt: "",
            name: "",
            allow_interrupt: getDefaultAllowInterrupt(NodeType.START_CALL),
        },
    },
];

const getNewNode = (type: string, position: { x: number, y: number }) => {
    return {
        id: `${getRandomId()}`,
        type,
        position,
        data: {
            prompt: {
                [NodeType.GLOBAL_NODE]: "You are a helpful assistant whose mode of interaction with the user is voice. So don't use any special characters which can not be pronounced. Use short sentences and simple language.",
            }[type] || "",
            name: {
                [NodeType.GLOBAL_NODE]: "Global Node",
                [NodeType.START_CALL]: "Start Call",
                [NodeType.END_CALL]: "End Call",
            }[type] || "",
            allow_interrupt: getDefaultAllowInterrupt(type),
        },
    };
};

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
}

export const useWorkflowState = ({ initialWorkflowName, workflowId, initialFlow, initialTemplateContextVariables, initialWorkflowConfigurations }: UseWorkflowStateProps) => {
    const rfInstance = useRef<ReactFlowInstance<FlowNode, FlowEdge> | null>(null);
    const router = useRouter();
    const { user, getAccessToken } = useAuth();
    const [nodes, setNodes] = useNodesState(
        initialFlow?.nodes?.length
            ? initialFlow.nodes.map(node => ({
                ...node,
                data: {
                    ...node.data,
                    invalid: false,
                    allow_interrupt: node.data.allow_interrupt !== undefined
                        ? node.data.allow_interrupt
                        : getDefaultAllowInterrupt(node.type),
                }
            }))
            : defaultNodes
    );
    const [edges, setEdges] = useEdgesState(initialFlow?.edges ?? []);
    const [isAddNodePanelOpen, setIsAddNodePanelOpen] = useState(false);
    const [workflowName, setWorkflowName] = useState(initialWorkflowName);
    const [isEditingName, setIsEditingName] = useState(false);
    const [isDirty, setIsDirty] = useState(false);
    const [workflowValidationErrors, setWorkflowValidationErrors] = useState<WorkflowError[]>([]);
    const [templateContextVariables, setTemplateContextVariables] = useState<Record<string, string>>(
        initialTemplateContextVariables || {}
    );
    const [workflowConfigurations, setWorkflowConfigurations] = useState<WorkflowConfigurations | null>(
        initialWorkflowConfigurations || DEFAULT_WORKFLOW_CONFIGURATIONS
    );

    const handleNodeSelect = useCallback((nodeType: string) => {
        /*
            Used to add new node to the workflow. Receives nodeType as param.
            Example: nodeType can be agentNode/ startNode etc. as defined by NodeType in
                types.ts

            We then pass nodeTypes which contais the NodeType keyword and the component.
            Those components then contain all the component speecific functioanlity like edit
            button etc.

        */
        const newNode = getNewNode(nodeType, { x: 150, y: 150 });
        setNodes((nds) => [...nds, newNode]);
        setIsAddNodePanelOpen(false);
    }, [setNodes, setIsAddNodePanelOpen]);

    const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setWorkflowName(e.target.value);
        setIsDirty(true);
    };

    // Validate workflow function (without saving)
    const validateWorkflow = useCallback(async () => {
        if (!user) return;
        try {
            const accessToken = await getAccessToken();
            const response = await validateWorkflowApiV1WorkflowWorkflowIdValidatePost({
                path: {
                    workflow_id: workflowId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            // Reset validation state for all nodes and edges
            setNodes((nds) => nds.map(node => ({ ...node, data: { ...node.data, invalid: false, validationMessage: null } })));
            setEdges((eds) => eds.map(edge => ({ ...edge, data: { ...edge.data, invalid: false, validationMessage: null } })));
            setWorkflowValidationErrors([]);

            // Check if we have a 422 error with validation errors
            if (response.error) {
                // The error could be in different formats depending on the status code
                let errors: WorkflowError[] = [];

                // Type assertion for validation response structure
                const errorResponse = response.error as {
                    is_valid?: boolean;
                    errors?: WorkflowError[];
                    detail?: { errors: WorkflowError[] };
                };

                // For 422 responses, the error contains the validation response
                if (errorResponse.is_valid === false && errorResponse.errors) {
                    errors = errorResponse.errors;
                }
                // Also check for detail.errors format
                else if (errorResponse.detail?.errors) {
                    errors = errorResponse.detail.errors;
                }

                if (errors.length > 0) {
                    // Update nodes with validation state
                    setNodes((nds) => nds.map(node => {
                        const nodeErrors = errors.filter((err) => err.kind === 'node' && err.id === node.id);
                        if (nodeErrors.length > 0) {
                            return {
                                ...node,
                                data: {
                                    ...node.data,
                                    invalid: true,
                                    validationMessage: nodeErrors.map(err => err.message).join(', ')
                                }
                            };
                        }
                        return node;
                    }));

                    // Update edges with validation state
                    setEdges((eds) => eds.map(edge => {
                        const edgeErrors = errors.filter((err) => err.kind === 'edge' && err.id === edge.id);
                        if (edgeErrors.length > 0) {
                            return {
                                ...edge,
                                data: {
                                    ...edge.data,
                                    invalid: true,
                                    validationMessage: edgeErrors.map(err => err.message).join(', ')
                                }
                            };
                        }
                        return edge;
                    }));

                    // Set workflow validation errors (all types of errors)
                    setWorkflowValidationErrors(errors);
                }
            } else if (response.data) {
                // If we get a 200 response with data, check if it's valid
                if (response.data.is_valid === false && response.data.errors) {
                    const errors = response.data.errors;

                    // Update nodes with validation state
                    setNodes((nds) => nds.map(node => {
                        const nodeErrors = errors.filter((err) => err.kind === 'node' && err.id === node.id);
                        if (nodeErrors.length > 0) {
                            return {
                                ...node,
                                data: {
                                    ...node.data,
                                    invalid: true,
                                    validationMessage: nodeErrors.map((err) => err.message).join(', ')
                                }
                            };
                        }
                        return node;
                    }));

                    // Update edges with validation state
                    setEdges((eds) => eds.map(edge => {
                        const edgeErrors = errors.filter((err) => err.kind === 'edge' && err.id === edge.id);
                        if (edgeErrors.length > 0) {
                            return {
                                ...edge,
                                data: {
                                    ...edge.data,
                                    invalid: true,
                                    validationMessage: edgeErrors.map((err) => err.message).join(', ')
                                }
                            };
                        }
                        return edge;
                    }));

                    // Set workflow validation errors (all types of errors)
                    setWorkflowValidationErrors(errors);
                } else {
                    logger.info('Workflow is valid');
                }
            }
        } catch (error) {
            logger.error(`Unexpected validation error: ${error}`);
        }
    }, [workflowId, user, getAccessToken, setNodes, setEdges]);

    // Save function
    const saveWorkflow = useCallback(async (updateWorkflowDefinition: boolean = true) => {
        /*
            validates and saves workflow
        */
        if (!user || !rfInstance.current) return;
        const flow = rfInstance.current.toObject();
        const accessToken = await getAccessToken();
        try {
            await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: {
                    workflow_id: workflowId,
                },
                body: {
                    name: workflowName,
                    workflow_definition: updateWorkflowDefinition ? flow : null,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });
            setIsDirty(false);
        } catch (error) {
            logger.error(`Error auto-saving workflow: ${error}`);
        }

        // Validate after saving
        await validateWorkflow();
    }, [workflowId, workflowName, setIsDirty, user, getAccessToken, rfInstance, validateWorkflow]);

    // Handle debounced save - REMOVED AUTOSAVE FUNCTIONALITY
    // const debouncedSave = useCallback(() => {
    //     // Clear any existing timeout
    //     if (saveTimeoutRef.current) {
    //         clearTimeout(saveTimeoutRef.current);
    //     }

    //     // Set a new timeout
    //     saveTimeoutRef.current = setTimeout(() => {
    //         saveWorkflow();
    //         saveTimeoutRef.current = null;
    //     }, 2000);
    // }, [saveWorkflow]);

    const onConnect: OnConnect = useCallback((connection) => {
        setEdges((eds) => addEdge({
            ...connection,
            data: {
                label: '',
                condition: ''
            }
        }, eds));
        setIsDirty(true);
        // Trigger validation after connection
        setTimeout(() => validateWorkflow(), 100);
    }, [setEdges, validateWorkflow]);

    const onEdgesChange: OnEdgesChange = useCallback(
        (changes) => setEdges((eds) => {
            const newEdges = applyEdgeChanges(changes, eds) as FlowEdge[];
            setIsDirty(true);
            console.log("in onEdgesChange", changes, eds, newEdges);
            return newEdges;
        }),
        [setEdges],
    );

    const onNodesChange: OnNodesChange = useCallback(
        (changes) => setNodes((nds) => {
            const newNodes = applyNodeChanges(changes, nds) as FlowNode[];
            setIsDirty(true);
            return newNodes;
        }),
        [setNodes],
    );

    const onRun = async (mode: string) => {
        if (!user) return;
        const workflowRunName = `WR-${getRandomId()}`;
        const accessToken = await getAccessToken();
        const response = await createWorkflowRunApiV1WorkflowWorkflowIdRunsPost({
            path: {
                workflow_id: workflowId,
            },
            body: {
                mode,
                name: workflowRunName
            },
            headers: {
                'Authorization': `Bearer ${accessToken}`,
            },
        });
        router.push(`/workflow/${workflowId}/run/${response.data?.id}`);
    };

    // Save template context variables function
    const saveTemplateContextVariables = useCallback(async (variables: Record<string, string>) => {
        if (!user) return;
        const accessToken = await getAccessToken();
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
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });
            setTemplateContextVariables(variables);
            logger.info('Template context variables saved successfully');
        } catch (error) {
            logger.error(`Error saving template context variables: ${error}`);
            throw error;
        }
    }, [workflowId, workflowName, user, getAccessToken]);

    // Save workflow configurations function
    const saveWorkflowConfigurations = useCallback(async (configurations: WorkflowConfigurations) => {
        if (!user) return;
        const accessToken = await getAccessToken();
        try {
            await updateWorkflowApiV1WorkflowWorkflowIdPut({
                path: {
                    workflow_id: workflowId,
                },
                body: {
                    name: workflowName,
                    workflow_definition: null,
                    workflow_configurations: configurations as Record<string, unknown>,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });
            setWorkflowConfigurations(configurations);
            logger.info('Workflow configurations saved successfully');
        } catch (error) {
            logger.error(`Error saving workflow configurations: ${error}`);
            throw error;
        }
    }, [workflowId, workflowName, user, getAccessToken]);

    // Validate workflow on mount
    useEffect(() => {
        validateWorkflow();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // Removed useEffect for clearing auto-save timeout as autosave is disabled

    return {
        rfInstance,
        nodes,
        edges,
        isAddNodePanelOpen,
        workflowName,
        isEditingName,
        isDirty,
        workflowValidationErrors,
        templateContextVariables,
        workflowConfigurations,
        setNodes,
        setEdges,
        setIsAddNodePanelOpen,
        setWorkflowName,
        setIsEditingName,
        handleNodeSelect,
        handleNameChange,
        saveWorkflow,
        onConnect,
        onEdgesChange,
        onNodesChange,
        onRun,
        saveTemplateContextVariables,
        saveWorkflowConfigurations
    };
};
