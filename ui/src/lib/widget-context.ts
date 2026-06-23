/**
 * Global widget context management exposed to the browser window.
 * 
 * This allows external JavaScript applications (that embed the dograh widget)
 * to pass custom variables into the agent call lifecycle.
 * 
 * @example Usage from embedding application:
 * ```javascript
 * // Set context variables before initiating the widget
 * window.dograhWidget?.setContextVariables({
 *   userId: "user_123",
 *   customerName: "Jane Smith",
 *   accountType: "premium",
 *   campaignId: "camp_789"
 * });
 * 
 * // Or update individual variables
 * window.dograhWidget?.updateContextVariable("userId", "user_456");
 * 
 * // Get current variables
 * const vars = window.dograhWidget?.getContextVariables();
 * ```
 */

export interface DograhWidgetAPI {
  setContextVariables: (variables: Record<string, any>) => void;
  updateContextVariable: (key: string, value: any) => void;
  getContextVariables: () => Record<string, any>;
  clearContextVariables: () => void;
}

let globalContextVariables: Record<string, any> = {};

export const widgetContextAPI: DograhWidgetAPI = {
  /**
   * Set all context variables at once.
   * Replaces any existing variables.
   */
  setContextVariables(variables: Record<string, any>) {
    globalContextVariables = { ...variables };
    console.debug("[dograh-widget] Context variables set:", globalContextVariables);
    // Dispatch custom event for any listeners
    window.dispatchEvent(
      new CustomEvent("dograh:context-changed", { detail: globalContextVariables })
    );
  },

  /**
   * Update a single context variable.
   * Merges with existing variables.
   */
  updateContextVariable(key: string, value: any) {
    globalContextVariables[key] = value;
    console.debug(`[dograh-widget] Context variable updated: ${key}`, value);
    window.dispatchEvent(
      new CustomEvent("dograh:context-changed", { detail: globalContextVariables })
    );
  },

  /**
   * Get all current context variables.
   */
  getContextVariables() {
    return { ...globalContextVariables };
  },

  /**
   * Clear all context variables.
   */
  clearContextVariables() {
    globalContextVariables = {};
    console.debug("[dograh-widget] Context variables cleared");
    window.dispatchEvent(
      new CustomEvent("dograh:context-changed", { detail: {} })
    );
  },
};

/**
 * Initialize the global widget context API on window object.
 * Call this once during app initialization.
 */
export function initializeWidgetContextAPI() {
  if (!window.dograhWidget) {
    (window as any).dograhWidget = {};
  }
  
  Object.assign((window as any).dograhWidget, widgetContextAPI);
  console.debug("[dograh-widget] Widget context API initialized");
}

/**
 * Get the current global context variables.
 * Used internally by the widget to access variables when initiating calls.
 */
export function getGlobalContextVariables(): Record<string, any> {
  return { ...globalContextVariables };
}
