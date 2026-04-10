"use client";

import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";

import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface UnsavedChangesContextValue {
    register: (id: string, isDirty: boolean) => void;
    unregister: (id: string) => void;
    hasDirtyChanges: boolean;
    dirtySections: Set<string>;
    /** Wrap programmatic navigation (e.g. router.push) to guard against unsaved changes. */
    confirmNavigate: (navigate: () => void) => void;
}

const UnsavedChangesContext = createContext<UnsavedChangesContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

/**
 * Wraps a page to guard against accidental navigation when sections have
 * unsaved changes. Intercepts:
 *
 *  - Browser back / forward       (`popstate` with history-state tracking)
 *  - In-app link clicks           (document-level click capture on `<a>` tags)
 *
 * Sections register via the `useUnsavedChanges` hook.
 */
export function UnsavedChangesProvider({ children }: { children: React.ReactNode }) {
    const [dirtySections, setDirtySections] = useState<Set<string>>(new Set());
    const [showDialog, setShowDialog] = useState(false);
    const pendingNavigate = useRef<(() => void) | null>(null);

    const hasDirtyChanges = dirtySections.size > 0;
    const hasDirtyRef = useRef(hasDirtyChanges);
    hasDirtyRef.current = hasDirtyChanges;

    // -- Section registration ------------------------------------------------

    const register = useCallback((id: string, isDirty: boolean) => {
        setDirtySections((prev) => {
            const next = new Set(prev);
            if (isDirty) next.add(id);
            else next.delete(id);
            return next;
        });
    }, []);

    const unregister = useCallback((id: string) => {
        setDirtySections((prev) => {
            if (!prev.has(id)) return prev;
            const next = new Set(prev);
            next.delete(id);
            return next;
        });
    }, []);

    // -- Helper: prompt or proceed -------------------------------------------

    const askOrProceed = useCallback((proceed: () => void) => {
        if (!hasDirtyRef.current) {
            proceed();
            return;
        }
        pendingNavigate.current = proceed;
        setTimeout(() => setShowDialog(true), 0);
    }, []);

    // -- 1. Intercept <a> clicks in capture phase -----------------------------
    //
    // Next.js <Link> renders <a> tags. By listening in the capture phase we
    // intercept the click before React / Next.js processes it. If the user
    // confirms, we navigate programmatically via window.location.

    useEffect(() => {
        const handleClick = (e: MouseEvent) => {
            if (!hasDirtyRef.current) return;

            const target = e.target as HTMLElement;
            const link = target.closest("a[href]") as HTMLAnchorElement | null;
            if (!link) return;

            const href = link.getAttribute("href");
            if (!href) return;

            // Skip external links
            if (href.startsWith("http://") || href.startsWith("https://") || href.startsWith("//")) return;
            // Skip hash-only links (in-page anchors)
            if (href.startsWith("#")) return;
            // Skip links that open in a new tab/window
            if (link.target && link.target !== "_self") return;
            // Skip download links
            if (link.hasAttribute("download")) return;
            // Skip if modifier keys are held (Ctrl+click, Cmd+click, etc.)
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            // Skip non-left clicks
            if (e.button !== 0) return;

            // Block the navigation and ask the user
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();

            askOrProceed(() => {
                // Navigate after user confirms
                window.location.href = href;
            });
        };

        // Capture phase so we fire before React / Next.js handlers
        document.addEventListener("click", handleClick, true);
        return () => document.removeEventListener("click", handleClick, true);
    }, [askOrProceed]);

    // -- 3. Browser back / forward (`popstate`) ------------------------------
    //
    // When the browser fires popstate the URL has already changed. We push
    // the current page back onto the stack to "undo" the navigation, then
    // show the dialog. If confirmed, we call history.back() for real.

    useLayoutEffect(() => {
        // Track our own history stack index so we can correctly reverse
        // back/forward regardless of how many entries deep we are.
        let stackIndex = (history.state?.__unsaved_guard_index as number) ?? 0;

        const originalPushState = history.pushState.bind(history);
        const originalReplaceState = history.replaceState.bind(history);

        // Augment pushState to track stack depth
        history.pushState = function (state, unused, url) {
            stackIndex++;
            const augmented = { ...state, __unsaved_guard_index: stackIndex };
            return originalPushState(augmented, unused, url);
        };

        history.replaceState = function (state, unused, url) {
            const augmented = { ...state, __unsaved_guard_index: stackIndex };
            return originalReplaceState(augmented, unused, url);
        };

        // Write initial index if not present
        if (history.state?.__unsaved_guard_index == null) {
            originalReplaceState(
                { ...history.state, __unsaved_guard_index: stackIndex },
                "",
                location.href,
            );
        }

        const handlePopState = (e: PopStateEvent) => {
            if (!hasDirtyRef.current) {
                // Not dirty — accept navigation, update our tracked index
                stackIndex = (e.state?.__unsaved_guard_index as number) ?? stackIndex;
                return;
            }

            const nextIndex = (e.state?.__unsaved_guard_index as number) ?? 0;
            const delta = nextIndex - stackIndex;

            if (delta === 0) return;

            // Undo the navigation the browser already did
            history.go(-delta);

            askOrProceed(() => {
                // User confirmed — replay the navigation
                stackIndex = nextIndex;
                history.go(delta);
            });
        };

        window.addEventListener("popstate", handlePopState);

        return () => {
            history.pushState = originalPushState;
            history.replaceState = originalReplaceState;
            window.removeEventListener("popstate", handlePopState);
        };
    }, [askOrProceed]);

    // -- Dialog handlers -----------------------------------------------------

    const handleConfirm = useCallback(() => {
        setShowDialog(false);
        const nav = pendingNavigate.current;
        pendingNavigate.current = null;
        nav?.();
    }, []);

    const handleCancel = useCallback(() => {
        setShowDialog(false);
        pendingNavigate.current = null;
    }, []);

    // -- Render --------------------------------------------------------------

    return (
        <UnsavedChangesContext.Provider
            value={{ register, unregister, hasDirtyChanges, dirtySections, confirmNavigate: askOrProceed }}
        >
            {children}

            <AlertDialog open={showDialog} onOpenChange={(open) => { if (!open) handleCancel(); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Unsaved changes</AlertDialogTitle>
                        <AlertDialogDescription>
                            You have unsaved changes that will be lost. Are you sure you want to leave?
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel onClick={handleCancel}>Stay on page</AlertDialogCancel>
                        <AlertDialogAction onClick={handleConfirm}>Discard changes</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </UnsavedChangesContext.Provider>
    );
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * Register a section's dirty state with the nearest UnsavedChangesProvider.
 * Automatically unregisters on unmount.
 *
 * @example
 * useUnsavedChanges("general", isDirty);
 */
export function useUnsavedChanges(sectionId: string, isDirty: boolean) {
    const ctx = useContext(UnsavedChangesContext);
    if (!ctx) throw new Error("useUnsavedChanges must be used within UnsavedChangesProvider");

    const { register, unregister } = ctx;

    useEffect(() => {
        register(sectionId, isDirty);
    }, [sectionId, isDirty, register]);

    useEffect(() => {
        return () => unregister(sectionId);
    }, [sectionId, unregister]);
}

/**
 * Access the unsaved-changes context directly (e.g. for dirtySections).
 */
export function useUnsavedChangesContext() {
    const ctx = useContext(UnsavedChangesContext);
    if (!ctx) throw new Error("useUnsavedChangesContext must be used within UnsavedChangesProvider");
    return ctx;
}
