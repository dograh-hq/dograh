"use client";

import { ReactNode, createContext, useContext, useState } from "react";

/**
 * Widget context variables - custom data passed from the embedding application
 * through JavaScript to be available throughout the agent call lifecycle.
 * 
 * These variables are sent with the initial WebSocket connection and stored
 * in the workflow run's initial_context for reference throughout execution.
 */
export interface WidgetContextVariables {
  // Custom key-value pairs passed by the embedding application
  [key: string]: any;
}

interface WidgetContextType {
  variables: WidgetContextVariables;
  setVariables: (vars: WidgetContextVariables) => void;
  updateVariable: (key: string, value: any) => void;
  clearVariables: () => void;
}

const WidgetContext = createContext<WidgetContextType | undefined>(undefined);

export function WidgetContextProvider({ children }: { children: ReactNode }) {
  const [variables, setVariables] = useState<WidgetContextVariables>({});

  const updateVariable = (key: string, value: any) => {
    setVariables((prev) => ({ ...prev, [key]: value }));
  };

  const clearVariables = () => {
    setVariables({});
  };

  return (
    <WidgetContext.Provider
      value={{
        variables,
        setVariables,
        updateVariable,
        clearVariables,
      }}
    >
      {children}
    </WidgetContext.Provider>
  );
}

/**
 * Hook to access and manage widget context variables
 * 
 * @example
 * const { variables, setVariables } = useWidgetContext();
 * setVariables({
 *   userId: "user_123",
 *   customerName: "John Doe",
 *   accountId: "acc_456"
 * });
 */
export function useWidgetContext() {
  const context = useContext(WidgetContext);
  if (!context) {
    throw new Error("useWidgetContext must be used within WidgetContextProvider");
  }
  return context;
}
