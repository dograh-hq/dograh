import { createContext, useContext } from 'react';

import type { DocumentResponseSchema, ToolResponse } from '@/client/types.gen';

interface WorkflowContextType {
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
    documents?: DocumentResponseSchema[];
    tools?: ToolResponse[];
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(undefined);

export const WorkflowProvider = WorkflowContext.Provider;

export const useWorkflow = () => {
    const context = useContext(WorkflowContext);
    if (!context) {
        throw new Error('useWorkflow must be used within a WorkflowProvider');
    }
    return context;
};

// Optional hook that doesn't throw if context is not available
export const useWorkflowOptional = () => {
    return useContext(WorkflowContext);
};
