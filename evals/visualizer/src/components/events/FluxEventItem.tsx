"use client";

import { CapturedEvent } from "@/types";

interface FluxEventItemProps {
  event: CapturedEvent;
  isExpanded: boolean;
  onToggleExpand: () => void;
}

const EVENT_COLORS: Record<string, string> = {
  TurnInfo: "text-green-400 bg-green-500/10",
  Connected: "text-yellow-400 bg-yellow-500/10",
  Error: "text-red-500 bg-red-600/10",
  default: "text-zinc-400 bg-zinc-500/10",
};

const FLUX_EVENT_COLORS: Record<string, string> = {
  Update: "text-amber-300 bg-amber-500/20",
  EndOfTurn: "text-emerald-300 bg-emerald-500/20",
  EagerEndOfTurn: "text-cyan-300 bg-cyan-500/20",
  StartOfTurn: "text-blue-300 bg-blue-500/20",
  TurnResumed: "text-purple-300 bg-purple-500/20",
  default: "text-zinc-300 bg-zinc-500/20",
};

export default function FluxEventItem({
  event,
  isExpanded,
  onToggleExpand,
}: FluxEventItemProps) {
  const colorClass = EVENT_COLORS[event.event_type] || EVENT_COLORS.default;
  const data = event.data;

  // Flux TurnInfo fields
  const fluxEvent = data.event as string | undefined;
  const transcript = data.transcript as string | undefined;
  const endOfTurnConfidence = data.end_of_turn_confidence as number | undefined;
  const turnIndex = data.turn_index as number | undefined;

  const isFinal = fluxEvent === "EndOfTurn";
  const fluxEventColor = fluxEvent
    ? FLUX_EVENT_COLORS[fluxEvent] || FLUX_EVENT_COLORS.default
    : "";

  // For non-TurnInfo events
  const isConnection = event.event_type === "Connected";

  return (
    <div className="flex-1 min-w-0 space-y-1">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-xs px-2 py-0.5 rounded ${colorClass}`}>
          {event.event_type}
        </span>

        {/* Flux sub-event type */}
        {fluxEvent && (
          <span className={`text-xs px-2 py-0.5 rounded ${fluxEventColor}`}>
            {fluxEvent}
          </span>
        )}

        {/* Final/Partial indicator */}
        {fluxEvent && (
          <span
            className={`text-xs px-2 py-0.5 rounded ${
              isFinal
                ? "text-emerald-400 bg-emerald-500/10"
                : "text-amber-400 bg-amber-500/10"
            }`}
          >
            {isFinal ? "Final" : "Partial"}
          </span>
        )}

        {/* Turn index */}
        {turnIndex !== undefined && (
          <span className="text-xs text-zinc-500">
            Turn {turnIndex}
          </span>
        )}

        {/* EOT confidence */}
        {endOfTurnConfidence !== undefined && (
          <span className="text-xs text-zinc-500 font-mono">
            EOT: {(endOfTurnConfidence * 100).toFixed(1)}%
          </span>
        )}
      </div>

      {/* Transcript or status message */}
      <div className="text-sm text-zinc-300 truncate">
        {transcript || (isConnection ? "[Connected]" : `[${fluxEvent || event.event_type}]`)}
      </div>

      {/* Expand/collapse button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onToggleExpand();
        }}
        className="text-xs text-zinc-500 hover:text-zinc-300"
      >
        {isExpanded ? "Hide details" : "Show details"}
      </button>

      {/* Expanded JSON view */}
      {isExpanded && (
        <pre className="mt-2 p-2 bg-zinc-900 rounded text-xs text-zinc-400 overflow-x-auto max-h-64">
          {JSON.stringify(event.data, null, 2)}
        </pre>
      )}
    </div>
  );
}
