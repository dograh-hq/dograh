"use client";

import { CapturedEvent } from "@/types";

interface DeepgramEventItemProps {
  event: CapturedEvent;
  isExpanded: boolean;
  onToggleExpand: () => void;
}

const EVENT_COLORS: Record<string, string> = {
  Results: "text-blue-400 bg-blue-500/10",
  SpeechStarted: "text-yellow-400 bg-yellow-500/10",
  Metadata: "text-gray-400 bg-gray-500/10",
  UtteranceEnd: "text-red-500 bg-red-600/10",
  default: "text-zinc-400 bg-zinc-500/10",
};

function getTranscript(event: CapturedEvent): string {
  const data = event.data;
  const channel = data.channel as Record<string, unknown> | undefined;
  if (channel) {
    const alternatives = channel.alternatives as Array<{ transcript?: string }> | undefined;
    if (alternatives?.[0]?.transcript) {
      return alternatives[0].transcript;
    }
  }
  return "";
}

export default function DeepgramEventItem({
  event,
  isExpanded,
  onToggleExpand,
}: DeepgramEventItemProps) {
  const colorClass = EVENT_COLORS[event.event_type] || EVENT_COLORS.default;
  const data = event.data;

  const transcript = getTranscript(event);
  const isFinal = data.is_final as boolean | undefined;
  const speechFinal = data.speech_final as boolean | undefined;

  // For non-Results events
  const isConnection = event.event_type === "Connected";
  const isMetadata = event.event_type === "Metadata";

  return (
    <div className="flex-1 min-w-0 space-y-1">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-xs px-2 py-0.5 rounded ${colorClass}`}>
          {event.event_type}
        </span>

        {/* Final/Partial indicator for Results */}
        {isFinal !== undefined && (
          <span
            className={`text-xs px-2 py-0.5 rounded ${isFinal
              ? "text-emerald-400 bg-emerald-500/10"
              : "text-amber-400 bg-amber-500/10"
              }`}
          >
            {isFinal ? "Final" : "Partial"}
          </span>
        )}

        {/* Speech Final indicator */}
        {speechFinal && (
          <span className="text-xs px-2 py-0.5 rounded text-cyan-400 bg-cyan-500/10">
            Speech Final
          </span>
        )}
      </div>

      {/* Transcript or status message */}
      <div className="text-sm text-zinc-300 truncate">
        {transcript}
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
