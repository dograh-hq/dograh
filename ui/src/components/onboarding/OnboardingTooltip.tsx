'use client';

import { arrow, autoUpdate, flip, offset, shift, useFloating } from '@floating-ui/react-dom';
import { X } from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { type TooltipKey, useOnboarding } from '@/context/OnboardingContext';

interface OnboardingTooltipProps {
    /** Onboarding flag this tooltip is keyed to. Visibility ("not seen yet")
     * and dismissal (mark seen, including when the target itself is clicked)
     * are derived from it, so call sites don't wire up the onboarding
     * context themselves. */
    tooltipKey: TooltipKey;
    targetRef: React.RefObject<HTMLElement | HTMLButtonElement | null>;
    title?: string;
    message: string;
    /** Extra gating beyond "not seen yet" (e.g. panel open, data loaded). */
    enabled?: boolean;
    onNext?: () => void;
    showNext?: boolean;
}

export const OnboardingTooltip = ({
    tooltipKey,
    targetRef,
    title = 'One more thing...',
    message,
    enabled = true,
    onNext,
    showNext = true,
}: OnboardingTooltipProps) => {
    const { hasSeenTooltip, markTooltipSeen } = useOnboarding();
    const arrowRef = useRef<HTMLDivElement>(null);
    const messageId = useId();
    const [mounted, setMounted] = useState(false);

    const isVisible = enabled && !hasSeenTooltip(tooltipKey);
    const dismiss = useCallback(() => markTooltipSeen(tooltipKey), [markTooltipSeen, tooltipKey]);

    const { refs, floatingStyles, middlewareData, placement, isPositioned, elements } = useFloating({
        placement: 'bottom',
        strategy: 'fixed',
        open: isVisible,
        // Tracks the target through scrolling (including nested overflow
        // containers), resizes, and layout shifts.
        whileElementsMounted: autoUpdate,
        middleware: [
            offset(8),
            flip({ padding: 16 }),
            shift({ padding: 16 }),
            arrow({ element: arrowRef }),
        ],
    });

    useEffect(() => {
        setMounted(true);
        return () => setMounted(false);
    }, []);

    // Adopt the target as the floating reference on every render: a ref prop
    // can't trigger effects when its element mounts late or remounts, and
    // setReference bails out when the element is unchanged.
    useEffect(() => {
        refs.setReference(targetRef.current);
    });

    // While pointing at the target: pulsate it, link it to the message for
    // screen readers, and treat a click on it as "seen".
    useEffect(() => {
        const target = elements.reference;
        if (!isVisible || !(target instanceof HTMLElement)) return;

        target.classList.add('onboarding-pulse');
        target.setAttribute('aria-describedby', messageId);
        target.addEventListener('click', dismiss);
        return () => {
            target.classList.remove('onboarding-pulse');
            target.removeAttribute('aria-describedby');
            target.removeEventListener('click', dismiss);
        };
    }, [isVisible, elements.reference, dismiss, messageId]);

    useEffect(() => {
        if (!isVisible) return;

        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') dismiss();
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [isVisible, dismiss]);

    if (!mounted || !isVisible) return null;

    // Actual side after flip(): 'bottom' (below target) or 'top' (above).
    const side = placement.split('-')[0] === 'top' ? 'top' : 'bottom';

    const tooltipContent = (
        <div
            ref={refs.setFloating}
            className={`z-[100] animate-in fade-in duration-300 ${side === 'bottom' ? 'slide-in-from-top-2' : 'slide-in-from-bottom-2'}`}
            style={{
                ...floatingStyles,
                // Avoid a flash at (0,0) before the first position resolves.
                visibility: isPositioned ? 'visible' : 'hidden',
            }}
        >
            {/* Arrow pointing at the target; floating-ui keeps it aligned even
                when the tooltip body is shifted to stay on-screen. */}
            <div
                ref={arrowRef}
                className="absolute h-4 w-4 rotate-45 bg-blue-500"
                style={{
                    left: middlewareData.arrow?.x != null ? `${middlewareData.arrow.x}px` : undefined,
                    ...(side === 'bottom' ? { top: '-8px' } : { bottom: '-8px' }),
                    boxShadow: '-2px -2px 4px rgba(0, 0, 0, 0.1)',
                }}
            />

            {/* Tooltip content */}
            <div className="relative bg-blue-500 text-white rounded-lg shadow-2xl p-6 max-w-sm">
                {/* Close button */}
                <button
                    onClick={dismiss}
                    className="absolute top-2 right-2 p-1 hover:bg-blue-600 rounded-full transition-colors"
                    aria-label="Close tooltip"
                >
                    <X className="h-4 w-4" />
                </button>

                {/* Title */}
                <h3 className="text-lg font-semibold mb-3">{title}</h3>

                {/* Message */}
                <p id={messageId} className="text-sm leading-relaxed mb-4 pr-4">
                    {message}
                </p>

                {/* Footer actions */}
                <div className="flex items-center justify-end gap-3">
                    <button
                        onClick={dismiss}
                        className="bg-white text-blue-500 px-4 py-1.5 rounded font-medium text-sm hover:bg-blue-50 transition-colors cursor-pointer"
                    >
                        Close
                    </button>

                    {showNext && (
                        <button
                            onClick={() => {
                                onNext?.();
                                dismiss();
                            }}
                            className="bg-white text-blue-500 px-4 py-1.5 rounded font-medium text-sm hover:bg-blue-50 transition-colors"
                        >
                            Next
                        </button>
                    )}
                </div>
            </div>
        </div>
    );

    // Use portal to render tooltip at document root
    return createPortal(tooltipContent, document.body);
};
