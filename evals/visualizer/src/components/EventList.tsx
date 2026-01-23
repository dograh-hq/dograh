"use client";

import { useEffect, useRef, useMemo, useState } from "react";
import { CapturedEvent } from "@/types";
import { DeepgramEventItem, FluxEventItem, SpeechmaticsEventItem } from "./events";

interface EventListProps {
  events: CapturedEvent[];
  currentTime: number;
  onSeek: (time: number) => void;
  provider: string;
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 100);
  return `${mins}:${secs.toString().padStart(2, "0")}.${ms.toString().padStart(2, "0")}`;
}

function getEventItemComponent(provider: string) {
  if (provider === "deepgram-flux") {
    return FluxEventItem;
  }
  if (provider === "speechmatics") {
    return SpeechmaticsEventItem;
  }
  // Default to Deepgram Nova
  return DeepgramEventItem;
}

export default function EventList({
  events,
  currentTime,
  onSeek,
  provider,
}: EventListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set());
  const [autoScroll, setAutoScroll] = useState(true);

  const EventItemComponent = getEventItemComponent(provider);

  // Find the current event index based on time
  const currentEventIndex = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].timestamp <= currentTime) {
        return i;
      }
    }
    return -1;
  }, [events, currentTime]);

  // Auto-scroll to current event
  useEffect(() => {
    if (!autoScroll || currentEventIndex < 0) return;

    const container = containerRef.current;
    if (!container) return;

    const eventElement = container.querySelector(`[data-index="${currentEventIndex}"]`);
    if (eventElement) {
      eventElement.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [currentEventIndex, autoScroll]);

  const toggleExpand = (index: number) => {
    setExpandedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  return (
    <div className="bg-zinc-800 rounded-lg flex flex-col h-full">
      <div className="flex justify-between items-center px-4 py-2 border-b border-zinc-700">
        <div className="text-sm text-zinc-400 font-medium">
          Events ({events.length})
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-500 cursor-pointer">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            className="rounded"
          />
          Auto-scroll
        </label>
      </div>

      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto divide-y divide-zinc-700/50"
      >
        {events.map((event, index) => {
          const isCurrent = index === currentEventIndex;
          const isExpanded = expandedEvents.has(index);

          return (
            <div
              key={index}
              data-index={index}
              className={`p-3 cursor-pointer transition-colors ${
                isCurrent ? "bg-zinc-700/50" : "hover:bg-zinc-700/30"
              }`}
              onClick={() => onSeek(event.timestamp)}
            >
              <div className="flex items-start gap-2">
                {/* Current indicator */}
                <div className="pt-1">
                  {isCurrent ? (
                    <div className="w-2 h-2 rounded-full bg-white" />
                  ) : (
                    <div className="w-2 h-2 rounded-full bg-zinc-600" />
                  )}
                </div>

                {/* Timestamp */}
                <span className="text-xs text-zinc-500 font-mono pt-0.5">
                  {formatTime(event.timestamp)}
                </span>

                {/* Provider-specific event item */}
                <EventItemComponent
                  event={event}
                  isExpanded={isExpanded}
                  onToggleExpand={() => toggleExpand(index)}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
