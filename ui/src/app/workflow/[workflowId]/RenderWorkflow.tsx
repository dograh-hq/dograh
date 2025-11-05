import '@xyflow/react/dist/style.css';

import {
    Background,
    BackgroundVariant,
    MiniMap,
    Panel,
    ReactFlow,
} from "@xyflow/react";
import { BrushCleaning, Maximize2, Minus, Plus, Settings, Variable } from 'lucide-react';
import { useMemo, useState } from 'react';

import WorkflowLayout from '@/app/workflow/WorkflowLayout';
import { FlowEdge, FlowNode, NodeType } from "@/components/flow/types";
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { WorkflowConfigurations } from '@/types/workflow-configurations';

import AddNodePanel from "../../../components/flow/AddNodePanel";
import CustomEdge from "../../../components/flow/edges/CustomEdge";
import { AgentNode, EndCall, GlobalNode, StartCall } from "../../../components/flow/nodes";
import { ConfigurationsDialog } from './components/ConfigurationsDialog';
import { TemplateContextVariablesDialog } from './components/TemplateContextVariablesDialog';
import { layoutNodes } from './components/WorkflowControls';
import WorkflowHeader from "./components/WorkflowHeader";
import { WorkflowTabs } from './components/WorkflowTabs';
import { WorkflowProvider } from "./contexts/WorkflowContext";
import { useWorkflowState } from "./hooks/useWorkflowState";

// Define the node types dynamically based on the onSave prop
const nodeTypes = {
    [NodeType.START_CALL]: StartCall,
    [NodeType.AGENT_NODE]: AgentNode,
    [NodeType.END_CALL]: EndCall,
    [NodeType.GLOBAL_NODE]: GlobalNode,
};

const edgeTypes = {
    custom: CustomEdge,
};

// Helper function for MiniMap node colors
const getNodeColor = (node: FlowNode) => {
    switch (node.type) {
        case NodeType.START_CALL:
            return '#10B981'; // green-500
        case NodeType.AGENT_NODE:
            return '#3B82F6'; // blue-500
        case NodeType.END_CALL:
            return '#EF4444'; // red-500
        case NodeType.GLOBAL_NODE:
            return '#F59E0B'; // orange-500
        default:
            return '#6B7280'; // gray-500
    }
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
}

function RenderWorkflow({ initialWorkflowName, workflowId, initialFlow, initialTemplateContextVariables, initialWorkflowConfigurations }: RenderWorkflowProps) {
    const [isContextVarsDialogOpen, setIsContextVarsDialogOpen] = useState(false);
    const [isConfigurationsDialogOpen, setIsConfigurationsDialogOpen] = useState(false);

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
        setIsAddNodePanelOpen,
        handleNodeSelect,
        saveWorkflow,
        onConnect,
        onEdgesChange,
        onNodesChange,
        onRun,
        saveTemplateContextVariables,
        saveWorkflowConfigurations
    } = useWorkflowState({ initialWorkflowName, workflowId, initialFlow, initialTemplateContextVariables, initialWorkflowConfigurations });

    // Memoize defaultEdgeOptions to prevent unnecessary re-renders
    const defaultEdgeOptions = useMemo(() => ({
        animated: true,
        type: "custom"
    }), []);

    const headerActions = (
        <WorkflowHeader
            workflowValidationErrors={workflowValidationErrors}
            isDirty={isDirty}
            workflowName={workflowName}
            rfInstance={rfInstance}
            onRun={onRun}
            workflowId={workflowId}
            saveWorkflow={saveWorkflow}
        />
    );

    const stickyTabs = <WorkflowTabs workflowId={workflowId} currentTab="editor" />;

    return (
        <WorkflowProvider value={{ saveWorkflow }}>
            <WorkflowLayout headerActions={headerActions} showFeaturesNav={false} stickyTabs={stickyTabs}>
                <div className="h-[calc(100vh-80px)] relative">
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
                        }}
                        defaultEdgeOptions={defaultEdgeOptions}
                    >
                        <Background
                            variant={BackgroundVariant.Dots}
                            gap={16}
                            size={1}
                            color="#94a3b8"
                        />
                        <MiniMap
                            nodeColor={getNodeColor}
                            position="bottom-right"
                            className="bg-white/90 border rounded shadow-lg"
                            maskColor="rgb(0, 0, 0, 0.1)"
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
                                </div>
                            </TooltipProvider>
                        </Panel>
                    </ReactFlow>

                    {/* Bottom-left controls - horizontal layout with custom buttons */}
                    <div className="absolute bottom-12 left-8 z-[1000] flex gap-2">
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
                                        onClick={() => setNodes(layoutNodes(nodes, edges, 'LR', rfInstance, saveWorkflow))}
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
                    onSave={saveWorkflowConfigurations}
                />

                <TemplateContextVariablesDialog
                    open={isContextVarsDialogOpen}
                    onOpenChange={setIsContextVarsDialogOpen}
                    templateContextVariables={templateContextVariables}
                    onSave={saveTemplateContextVariables}
                />
            </WorkflowLayout>
        </WorkflowProvider>
    );
}

export default RenderWorkflow;
