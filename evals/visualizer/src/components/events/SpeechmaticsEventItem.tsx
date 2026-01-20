"use client";

import { CapturedEvent } from "@/types";

interface SpeechmaticsEventItemProps {
  event: CapturedEvent;
  isExpanded: boolean;
  onToggleExpand: () => void;
}

const EVENT_COLORS: Record<string, string> = {
  AddTranscript: "text-purple-400 bg-purple-500/10",
  RecognitionStarted: "text-yellow-400 bg-yellow-500/10",
  EndOfTranscript: "text-red-400 bg-red-500/10",
  Warning: "text-orange-400 bg-orange-500/10",
  Error: "text-red-500 bg-red-600/10",
  default: "text-zinc-400 bg-zinc-500/10",
};

function getTranscript(event: CapturedEvent): string {
  const data = event.data;
  const results = data.results as Array<{
    type?: string;
    alternatives?: Array<{ content?: string }>;
  }> | undefined;

  if (results) {
    const words = results
      .filter((r) => r.type === "word" && r.alternatives?.[0]?.content)
      .map((r) => r.alternatives![0].content)
      .join(" ");
    return words;
  }
  return "";
}

export default function SpeechmaticsEventItem({
  event,
  isExpanded,
  onToggleExpand,
}: SpeechmaticsEventItemProps) {
  const colorClass = EVENT_COLORS[event.event_type] || EVENT_COLORS.default;
  const data = event.data;

  const transcript = getTranscript(event);

  // Status events
  const isRecognitionStarted = event.event_type === "RecognitionStarted";
  const isEndOfTranscript = event.event_type === "EndOfTranscript";
  const isWarning = event.event_type === "Warning";

  // Warning reason
  const warningReason = isWarning ? (data.reason as string | undefined) : undefined;

  return (
    <div className="flex-1 min-w-0 space-y-1">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-xs px-2 py-0.5 rounded ${colorClass}`}>
          {event.event_type}
        </span>

        {/* AddTranscript is always final in Speechmatics */}
        {event.event_type === "AddTranscript" && (
          <span className="text-xs px-2 py-0.5 rounded text-emerald-400 bg-emerald-500/10">
            Final
          </span>
        )}
      </div>

      {/* Transcript or status message */}
      <div className="text-sm text-zinc-300 truncate">
        {transcript ||
          (isRecognitionStarted
            ? "[Recognition Started]"
            : isEndOfTranscript
              ? "[End of Transcript]"
              : isWarning
                ? `[Warning: ${warningReason || "unknown"}]`
                : `[${event.event_type}]`)}
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
