'use client';

import { X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

interface OnboardingTooltipProps {
    targetRef: React.RefObject<HTMLElement | HTMLButtonElement | null>;
    title?: string;
    message: string;
    onDismiss: () => void;
    onNext?: () => void;
    showNext?: boolean;
    isVisible: boolean;
}

export const OnboardingTooltip = ({
    targetRef,
    title = "One more thing...",
    message,
    onDismiss,
    onNext,
    showNext = true,
    isVisible
}: OnboardingTooltipProps) => {
    const tooltipRef = useRef<HTMLDivElement>(null);
    const [position, setPosition] = useState({ top: 0, left: 0 });
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
        return () => setMounted(false);
    }, []);

    useEffect(() => {
        if (!isVisible || !targetRef.current) return;

        const calculatePosition = () => {
            if (!targetRef.current || !tooltipRef.current) return;

            const targetRect = targetRef.current.getBoundingClientRect();
            const tooltipRect = tooltipRef.current.getBoundingClientRect();

            // Position tooltip below the target element with some offset for the arrow
            const top = targetRect.bottom + 8; // 8px gap for arrow

            // Center the tooltip horizontally relative to the target
            let left = targetRect.left + (targetRect.width / 2) - (tooltipRect.width / 2);

            // Ensure tooltip doesn't go off-screen
            const padding = 16;
            if (left < padding) {
                left = padding;
            } else if (left + tooltipRect.width > window.innerWidth - padding) {
                left = window.innerWidth - tooltipRect.width - padding;
            }

            setPosition({ top, left });
        };

        // Small delay to ensure tooltip is rendered before calculating position
        const timer = setTimeout(() => {
            calculatePosition();
        }, 10);

        // Recalculate on window resize
        window.addEventListener('resize', calculatePosition);
        window.addEventListener('scroll', calculatePosition);

        return () => {
            clearTimeout(timer);
            window.removeEventListener('resize', calculatePosition);
            window.removeEventListener('scroll', calculatePosition);
        };
    }, [isVisible, targetRef]);

    if (!mounted || !isVisible) return null;

    const tooltipContent = (
        <div
            ref={tooltipRef}
            className="fixed z-[100] animate-in fade-in slide-in-from-top-2 duration-300"
            style={{ top: `${position.top}px`, left: `${position.left}px` }}
        >
            {/* Arrow pointing up */}
            <div
                className="absolute -top-2 left-1/2 -translate-x-1/2 w-4 h-4 bg-zinc-900 rotate-45"
                style={{
                    boxShadow: '-2px -2px 4px rgba(0, 0, 0, 0.1)'
                }}
            />

            {/* Tooltip content */}
            <div className="relative bg-zinc-900 text-white rounded-lg shadow-2xl p-6 max-w-sm">
                {/* Close button */}
                <button
                    onClick={onDismiss}
                    className="absolute top-2 right-2 p-1 hover:bg-zinc-700 rounded-full transition-colors"
                    aria-label="Close tooltip"
                >
                    <X className="h-4 w-4" />
                </button>

                {/* Title */}
                <h3 className="text-lg font-semibold mb-3">{title}</h3>

                {/* Message */}
                <p className="text-sm leading-relaxed mb-4 pr-4">
                    {message}
                </p>

                {/* Footer actions */}
                <div className="flex items-center justify-end gap-3">
                    <button
                        onClick={onDismiss}
                        className="bg-white text-zinc-900 px-4 py-1.5 rounded font-medium text-sm hover:bg-zinc-100 transition-colors cursor-pointer"
                    >
                        Close
                    </button>

                    {showNext && (
                        <button
                            onClick={() => {
                                onNext?.();
                                onDismiss();
                            }}
                            className="bg-white text-zinc-900 px-4 py-1.5 rounded font-medium text-sm hover:bg-zinc-100 transition-colors"
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
