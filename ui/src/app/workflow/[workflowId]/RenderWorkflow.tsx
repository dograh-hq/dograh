import '@xyflow/react/dist/style.css';

import {
    Background,
    BackgroundVariant,
    Panel,
    ReactFlow,
} from "@xyflow/react";
import { BrushCleaning, Maximize2, Minus, Plus, Rocket, Settings, Variable } from 'lucide-react';
import React, { useMemo, useState } from 'react';

import { FlowEdge, FlowNode, NodeType } from "@/components/flow/types";
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { WorkflowConfigurations } from '@/types/workflow-configurations';

import AddNodePanel from "../../../components/flow/AddNodePanel";
import CustomEdge from "../../../components/flow/edges/CustomEdge";
import { AgentNode, EndCall, GlobalNode, StartCall, TriggerNode, WebhookNode } from "../../../components/flow/nodes";
import { ConfigurationsDialog } from './components/ConfigurationsDialog';
import { EmbedDialog } from './components/EmbedDialog';
import { PhoneCallDialog } from './components/PhoneCallDialog';
import { TemplateContextVariablesDialog } from './components/TemplateContextVariablesDialog';
import { WorkflowEditorHeader } from "./components/WorkflowEditorHeader";
import { WorkflowProvider } from "./contexts/WorkflowContext";
import { useWorkflowState } from "./hooks/useWorkflowState";
import { layoutNodes } from './utils/layoutNodes';

// Define the node types dynamically based on the onSave prop
const nodeTypes = {
    [NodeType.START_CALL]: StartCall,
    [NodeType.AGENT_NODE]: AgentNode,
    [NodeType.END_CALL]: EndCall,
    [NodeType.GLOBAL_NODE]: GlobalNode,
    [NodeType.TRIGGER]: TriggerNode,
    [NodeType.WEBHOOK]: WebhookNode,
};

const edgeTypes = {
    custom: CustomEdge,
};

interface RenderWorkflowProps {
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
    user: { id: string; email?: string };
    getAccessToken: () => Promise<string>;
}

function RenderWorkflow({ initialWorkflowName, workflowId, initialFlow, initialTemplateContextVariables, initialWorkflowConfigurations, user, getAccessToken }: RenderWorkflowProps) {
    const [isContextVarsDialogOpen, setIsContextVarsDialogOpen] = useState(false);
    const [isConfigurationsDialogOpen, setIsConfigurationsDialogOpen] = useState(false);
    const [isEmbedDialogOpen, setIsEmbedDialogOpen] = useState(false);
    const [isPhoneCallDialogOpen, setIsPhoneCallDialogOpen] = useState(false);

    const {
        rfInstance,
        nodes,
        edges,
        isAddNodePanelOpen,
        workflowName,
        isDirty,
        workflowValidationErrors,
        templateContextVariables,
        workflowConfigurations,
        setNodes,
        setIsDirty,
        setIsAddNodePanelOpen,
        handleNodeSelect,
        saveWorkflow,
        onConnect,
        onEdgesChange,
        onNodesChange,
        onRun,
        saveTemplateContextVariables,
        saveWorkflowConfigurations
    } = useWorkflowState({
        initialWorkflowName,
        workflowId,
        initialFlow,
        initialTemplateContextVariables,
        initialWorkflowConfigurations,
        user,
        getAccessToken
    });

    // Memoize defaultEdgeOptions to prevent unnecessary re-renders
    const defaultEdgeOptions = useMemo(() => ({
        animated: true,
        type: "custom"
    }), []);

    // Memoize the context value to prevent unnecessary re-renders
    const workflowContextValue = useMemo(() => ({ saveWorkflow }), [saveWorkflow]);

    return (
        <WorkflowProvider value={workflowContextValue}>
            <div className="flex flex-col h-screen">
                {/* New Workflow Editor Header */}
                <WorkflowEditorHeader
                    workflowName={workflowName}
                    isDirty={isDirty}
                    workflowValidationErrors={workflowValidationErrors}
                    rfInstance={rfInstance}
                    onRun={onRun}
                    workflowId={workflowId}
                    saveWorkflow={saveWorkflow}
                    user={user}
                    getAccessToken={getAccessToken}
                    onPhoneCallClick={() => setIsPhoneCallDialogOpen(true)}
                />

                {/* Workflow Canvas */}
                <div className="flex-1 relative">
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        onNodesChange={onNodesChange}
                        onEdgesChange={onEdgesChange}
                        nodeTypes={nodeTypes}
                        edgeTypes={edgeTypes}
                        onConnect={onConnect}
                        onInit={(instance) => {
                            rfInstance.current = instance;
                            // Center the workflow on load
                            setTimeout(() => {
                                instance.fitView({ padding: 0.2, duration: 200, maxZoom: 0.75 });
                            }, 0);
                        }}
                        defaultEdgeOptions={defaultEdgeOptions}
                        defaultViewport={initialFlow?.viewport}
                    >
                        <Background
                            variant={BackgroundVariant.Dots}
                            gap={16}
                            size={1}
                            color="#94a3b8"
                        />

                        {/* Top-right controls - vertical layout */}
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
                                                onClick={() => setIsConfigurationsDialogOpen(true)}
                                                className="bg-white shadow-sm hover:shadow-md"
                                            >
                                                <Settings className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="left">
                                            <p>Configurations</p>
                                        </TooltipContent>
                                    </Tooltip>

                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Button
                                                variant="outline"
                                                size="icon"
                                                onClick={() => setIsContextVarsDialogOpen(true)}
                                                className="bg-white shadow-sm hover:shadow-md"
                                            >
                                                <Variable className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="left">
                                            <p>Template Context Variables</p>
                                        </TooltipContent>
                                    </Tooltip>

                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Button
                                                variant="outline"
                                                size="icon"
                                                onClick={() => setIsEmbedDialogOpen(true)}
                                                className="bg-white shadow-sm hover:shadow-md"
                                            >
                                                <Rocket className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent side="left">
                                            <p>Deploy Workflow</p>
                                        </TooltipContent>
                                    </Tooltip>
                                </div>
                            </TooltipProvider>
                        </Panel>
                    </ReactFlow>

                    {/* Bottom-left controls - horizontal layout with custom buttons */}
                    <div className="absolute bottom-12 left-8 z-10 flex gap-2">
                        <TooltipProvider>
                            {/* Zoom In */}
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

                            {/* Zoom Out */}
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

                            {/* Fit View */}
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

                            {/* Tidy/Arrange Nodes */}
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
                        </TooltipProvider>
                    </div>
                </div>

                <AddNodePanel
                    isOpen={isAddNodePanelOpen}
                    onNodeSelect={handleNodeSelect}
                    onClose={() => setIsAddNodePanelOpen(false)}
                />

                <ConfigurationsDialog
                    open={isConfigurationsDialogOpen}
                    onOpenChange={setIsConfigurationsDialogOpen}
                    workflowConfigurations={workflowConfigurations}
                    workflowName={workflowName}
                    onSave={saveWorkflowConfigurations}
                />

                <TemplateContextVariablesDialog
                    open={isContextVarsDialogOpen}
                    onOpenChange={setIsContextVarsDialogOpen}
                    templateContextVariables={templateContextVariables}
                    onSave={saveTemplateContextVariables}
                />

                <EmbedDialog
                    open={isEmbedDialogOpen}
                    onOpenChange={setIsEmbedDialogOpen}
                    workflowId={workflowId}
                    workflowName={workflowName}
                    getAccessToken={getAccessToken}
                />

                <PhoneCallDialog
                    open={isPhoneCallDialogOpen}
                    onOpenChange={setIsPhoneCallDialogOpen}
                    workflowId={workflowId}
                    getAccessToken={getAccessToken}
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
        prevProps.user.id === nextProps.user.id &&
        prevProps.getAccessToken === nextProps.getAccessToken
        // Note: We intentionally don't compare initialFlow, initialTemplateContextVariables,
        // or initialWorkflowConfigurations because they're only used for initialization
    );
});
