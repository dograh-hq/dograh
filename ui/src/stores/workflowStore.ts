import { ReactFlowInstance } from '@xyflow/react';
import { create } from 'zustand';

import { WorkflowError } from '@/client/types.gen';
import { FlowEdge, FlowNode } from '@/components/flow/types';
import { DEFAULT_WORKFLOW_CONFIGURATIONS, WorkflowConfigurations } from '@/types/workflow-configurations';

interface HistoryState {
  nodes: FlowNode[];
  edges: FlowEdge[];
  workflowName: string;
}

interface WorkflowState {
  // Workflow identification
  workflowId: number | null;
  workflowName: string;

  // Flow state
  nodes: FlowNode[];
  edges: FlowEdge[];

  // History for undo/redo
  history: HistoryState[];
  historyIndex: number;

  // UI state (not tracked in history)
  isDirty: boolean;
  isAddNodePanelOpen: boolean;
  isEditingName: boolean;

  // Validation state
  workflowValidationErrors: WorkflowError[];

  // Configuration
  templateContextVariables: Record<string, string>;
  workflowConfigurations: WorkflowConfigurations | null;

  // ReactFlow instance reference
  rfInstance: ReactFlowInstance<FlowNode, FlowEdge> | null;
}

interface WorkflowActions {
  // Initialization
  initializeWorkflow: (
    workflowId: number,
    workflowName: string,
    nodes: FlowNode[],
    edges: FlowEdge[],
    templateContextVariables?: Record<string, string>,
    workflowConfigurations?: WorkflowConfigurations | null
  ) => void;

  // History management
  pushToHistory: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;

  // Node operations
  setNodes: (nodes: FlowNode[] | ((nodes: FlowNode[]) => FlowNode[])) => void;
  addNode: (node: FlowNode) => void;
  updateNode: (nodeId: string, updates: Partial<FlowNode>) => void;
  deleteNode: (nodeId: string) => void;

  // Edge operations
  setEdges: (edges: FlowEdge[] | ((edges: FlowEdge[]) => FlowEdge[])) => void;
  addEdge: (edge: FlowEdge) => void;
  updateEdge: (edgeId: string, updates: Partial<FlowEdge>) => void;
  deleteEdge: (edgeId: string) => void;

  // Workflow metadata
  setWorkflowName: (name: string) => void;
  setTemplateContextVariables: (variables: Record<string, string>) => void;
  setWorkflowConfigurations: (configurations: WorkflowConfigurations) => void;

  // UI state
  setIsDirty: (isDirty: boolean) => void;
  setIsAddNodePanelOpen: (isOpen: boolean) => void;
  setIsEditingName: (isEditing: boolean) => void;

  // Validation
  setWorkflowValidationErrors: (errors: WorkflowError[]) => void;
  markNodeAsInvalid: (nodeId: string, message: string) => void;
  markEdgeAsInvalid: (edgeId: string, message: string) => void;
  clearValidationErrors: () => void;

  // ReactFlow instance
  setRfInstance: (instance: ReactFlowInstance<FlowNode, FlowEdge> | null) => void;

  // Clear store (for cleanup)
  clearStore: () => void;
}

type WorkflowStore = WorkflowState & WorkflowActions;

const MAX_HISTORY_SIZE = 50;

// Create the store
export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
  // Initial state
  workflowId: null,
  workflowName: '',
  nodes: [],
  edges: [],
  history: [],
  historyIndex: -1,
  isDirty: false,
  isAddNodePanelOpen: false,
  isEditingName: false,
  workflowValidationErrors: [],
  templateContextVariables: {},
  workflowConfigurations: DEFAULT_WORKFLOW_CONFIGURATIONS,
  rfInstance: null,

  // Actions
  initializeWorkflow: (workflowId, workflowName, nodes, edges, templateContextVariables = {}, workflowConfigurations = DEFAULT_WORKFLOW_CONFIGURATIONS) => {
    const initialHistory: HistoryState = { nodes, edges, workflowName };
    set({
      workflowId,
      workflowName,
      nodes,
      edges,
      templateContextVariables,
      workflowConfigurations,
      isDirty: false,
      workflowValidationErrors: [],
      history: [initialHistory],
      historyIndex: 0,
    });
  },

  pushToHistory: () => {
    const state = get();
    const currentState: HistoryState = {
      nodes: state.nodes,
      edges: state.edges,
      workflowName: state.workflowName,
    };

    // Remove any forward history if we're not at the end
    const newHistory = state.history.slice(0, state.historyIndex + 1);
    newHistory.push(currentState);

    // Limit history size
    if (newHistory.length > MAX_HISTORY_SIZE) {
      newHistory.shift();
    }

    set({
      history: newHistory,
      historyIndex: newHistory.length - 1,
    });
  },

  undo: () => {
    const state = get();
    if (state.historyIndex > 0) {
      const newIndex = state.historyIndex - 1;
      const historicState = state.history[newIndex];
      set({
        nodes: historicState.nodes,
        edges: historicState.edges,
        workflowName: historicState.workflowName,
        historyIndex: newIndex,
        isDirty: true,
      });
    }
  },

  redo: () => {
    const state = get();
    if (state.historyIndex < state.history.length - 1) {
      const newIndex = state.historyIndex + 1;
      const historicState = state.history[newIndex];
      set({
        nodes: historicState.nodes,
        edges: historicState.edges,
        workflowName: historicState.workflowName,
        historyIndex: newIndex,
        isDirty: true,
      });
    }
  },

  canUndo: () => {
    const state = get();
    return state.historyIndex > 0;
  },

  canRedo: () => {
    const state = get();
    return state.historyIndex < state.history.length - 1;
  },

  setNodes: (nodes) => {
    const state = get();
    let newNodes: FlowNode[];
    if (typeof nodes === 'function') {
      newNodes = nodes(state.nodes);
    } else {
      newNodes = nodes;
    }

    // Push current state to history before making changes
    get().pushToHistory();

    set({ nodes: newNodes, isDirty: true });
  },

  addNode: (node) => {
    const state = get();
    get().pushToHistory();
    set({
      nodes: [...state.nodes, node],
      isDirty: true
    });
  },

  updateNode: (nodeId, updates) => {
    const state = get();
    get().pushToHistory();
    set({
      nodes: state.nodes.map((node) =>
        node.id === nodeId ? { ...node, ...updates } : node
      ),
      isDirty: true,
    });
  },

  deleteNode: (nodeId) => {
    const state = get();
    get().pushToHistory();
    set({
      nodes: state.nodes.filter((node) => node.id !== nodeId),
      edges: state.edges.filter(
        (edge) => edge.source !== nodeId && edge.target !== nodeId
      ),
      isDirty: true,
    });
  },

  setEdges: (edges) => {
    const state = get();
    let newEdges: FlowEdge[];
    if (typeof edges === 'function') {
      newEdges = edges(state.edges);
    } else {
      newEdges = edges;
    }

    // Push current state to history before making changes
    get().pushToHistory();

    set({ edges: newEdges, isDirty: true });
  },

  addEdge: (edge) => {
    const state = get();
    get().pushToHistory();
    set({
      edges: [...state.edges, edge],
      isDirty: true
    });
  },

  updateEdge: (edgeId, updates) => {
    const state = get();
    get().pushToHistory();
    set({
      edges: state.edges.map((edge) =>
        edge.id === edgeId ? { ...edge, ...updates } : edge
      ),
      isDirty: true,
    });
  },

  deleteEdge: (edgeId) => {
    const state = get();
    get().pushToHistory();
    set({
      edges: state.edges.filter((edge) => edge.id !== edgeId),
      isDirty: true,
    });
  },

  setWorkflowName: (workflowName) => {
    get().pushToHistory();
    set({ workflowName, isDirty: true });
  },

  setTemplateContextVariables: (templateContextVariables) => {
    set({ templateContextVariables });
  },

  setWorkflowConfigurations: (workflowConfigurations) => {
    set({ workflowConfigurations });
  },

  setIsDirty: (isDirty) => {
    set({ isDirty });
  },

  setIsAddNodePanelOpen: (isAddNodePanelOpen) => {
    set({ isAddNodePanelOpen });
  },

  setIsEditingName: (isEditingName) => {
    set({ isEditingName });
  },

  setWorkflowValidationErrors: (workflowValidationErrors) => {
    set({ workflowValidationErrors });
  },

  markNodeAsInvalid: (nodeId, message) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, invalid: true, validationMessage: message } }
          : node
      ),
    }));
  },

  markEdgeAsInvalid: (edgeId, message) => {
    set((state) => ({
      edges: state.edges.map((edge) =>
        edge.id === edgeId
          ? { ...edge, data: { ...edge.data, invalid: true, validationMessage: message } }
          : edge
      ),
    }));
  },

  clearValidationErrors: () => {
    set((state) => ({
      nodes: state.nodes.map((node) => ({
        ...node,
        data: { ...node.data, invalid: false, validationMessage: null },
      })),
      edges: state.edges.map((edge) => ({
        ...edge,
        data: { ...edge.data, invalid: false, validationMessage: null },
      })),
      workflowValidationErrors: [],
    }));
  },

  setRfInstance: (rfInstance) => {
    set({ rfInstance });
  },

  clearStore: () => {
    set({
      workflowId: null,
      workflowName: '',
      nodes: [],
      edges: [],
      history: [],
      historyIndex: -1,
      isDirty: false,
      isAddNodePanelOpen: false,
      isEditingName: false,
      workflowValidationErrors: [],
      templateContextVariables: {},
      workflowConfigurations: DEFAULT_WORKFLOW_CONFIGURATIONS,
      rfInstance: null,
    });
  },
}));

// Selectors for common use cases
export const useWorkflowNodes = () => useWorkflowStore((state) => state.nodes);
export const useWorkflowEdges = () => useWorkflowStore((state) => state.edges);
export const useWorkflowName = () => useWorkflowStore((state) => state.workflowName);
export const useWorkflowId = () => useWorkflowStore((state) => state.workflowId);
export const useWorkflowDirtyState = () => useWorkflowStore((state) => state.isDirty);
export const useWorkflowValidationErrors = () => useWorkflowStore((state) => state.workflowValidationErrors);

// Selector for undo/redo state
export const useUndoRedo = () => {
  const undo = useWorkflowStore((state) => state.undo);
  const redo = useWorkflowStore((state) => state.redo);
  const canUndo = useWorkflowStore((state) => state.canUndo());
  const canRedo = useWorkflowStore((state) => state.canRedo());

  return { undo, redo, canUndo, canRedo };
};