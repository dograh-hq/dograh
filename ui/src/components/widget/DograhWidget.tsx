"use client";

/**
 * Main Dograh Widget Component
 * 
 * This component manages the WebSocket connection to the dograh backend
 * and handles workflow run initialization with context variables.
 * 
 * Context variables passed via JavaScript are automatically included
 * in the initial request and stored in the workflow run for agent reference.
 */

import { useEffect, useRef, useState } from "react";
import { getGlobalContextVariables } from "@/lib/widget-context";
import { toast } from "sonner";

interface WorkflowRunRequest {
  workflow_id: string;
  // Context variables from the embedding application
  initial_context?: Record<string, any>;
  // Other workflow configuration...
}

interface DograhWidgetProps {
  workflowId: string;
  baseUrl?: string;
  onReady?: () => void;
  onError?: (error: Error) => void;
}

export function DograhWidget({
  workflowId,
  baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  onReady,
  onError,
}: DograhWidgetProps) {
  const [isConnected, setIsConnected] = useState(false);
  const [isInitialized, setIsInitialized] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const runIdRef = useRef<string | null>(null);

  useEffect(() => {
    const initializeWidget = async () => {
      try {
        // Get context variables from global widget API
        const contextVariables = getGlobalContextVariables();
        
        console.debug("[DograhWidget] Initializing with context:", contextVariables);

        // Initiate workflow run with context
        await startWorkflowRun(workflowId, contextVariables);
        
        setIsInitialized(true);
        onReady?.();
      } catch (error) {
        const err = error instanceof Error ? error : new Error(String(error));
        console.error("[DograhWidget] Initialization error:", err);
        onError?.(err);
        toast.error("Failed to initialize widget");
      }
    };

    initializeWidget();

    // Cleanup on unmount
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [workflowId, onReady, onError]);

  const startWorkflowRun = async (
    workflow_id: string,
    contextVariables: Record<string, any>
  ) => {
    try {
      // Call backend to create workflow run with initial context
      const response = await fetch(`${baseUrl}/api/v1/workflows/${workflow_id}/runs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${process.env.NEXT_PUBLIC_API_TOKEN}`,
        },
        body: JSON.stringify({
          initial_context: contextVariables,
        } as WorkflowRunRequest),
      });

      if (!response.ok) {
        throw new Error(`Failed to create workflow run: ${response.statusText}`);
      }

      const data = await response.json();
      runIdRef.current = data.run_id;

      // Connect WebSocket for real-time streaming
      connectWebSocket(data.run_id);
      setIsConnected(true);
    } catch (error) {
      console.error("[DograhWidget] Error starting workflow run:", error);
      throw error;
    }
  };

  const connectWebSocket = (runId: string) => {
    const wsUrl = `${baseUrl.replace(/^http/, "ws")}/api/v1/workflows/runs/${runId}/ws`;
    
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.debug("[DograhWidget] WebSocket connected");
      
      // Send authentication if needed
      ws.send(JSON.stringify({
        type: "auth",
        token: process.env.NEXT_PUBLIC_API_TOKEN,
      }));
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message);
      } catch (error) {
        console.error("[DograhWidget] Error parsing WebSocket message:", error);
      }
    };

    ws.onerror = (error) => {
      console.error("[DograhWidget] WebSocket error:", error);
      onError?.(new Error("WebSocket connection error"));
    };

    ws.onclose = () => {
      console.debug("[DograhWidget] WebSocket closed");
      setIsConnected(false);
    };

    wsRef.current = ws;
  };

  const handleWebSocketMessage = (message: any) => {
    switch (message.type) {
      case "agent_response":
        console.debug("[DograhWidget] Agent response:", message);
        // Handle agent response
        break;
      
      case "workflow_completed":
        console.debug("[DograhWidget] Workflow completed");
        // Handle workflow completion
        break;
      
      case "error":
        console.error("[DograhWidget] Agent error:", message);
        toast.error(message.detail || "An error occurred");
        break;
      
      default:
        console.debug("[DograhWidget] Unhandled message type:", message.type);
    }
  };

  return (
    <div className="dograh-widget">
      {!isInitialized && (
        <div className="flex items-center justify-center p-4">
          <div className="text-center">
            <div className="mb-2 inline-block animate-spin">
              <div className="h-8 w-8 border-4 border-primary border-t-transparent rounded-full" />
            </div>
            <p className="text-sm text-muted-foreground">Initializing widget...</p>
          </div>
        </div>
      )}

      {isInitialized && isConnected && (
        <div className="widget-content">
          {/* Widget content renders here */}
        </div>
      )}
    </div>
  );
}
