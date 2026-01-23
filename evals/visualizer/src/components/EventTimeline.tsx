"use client";

import { useMemo } from "react";
import { CapturedEvent } from "@/types";

interface EventTimelineProps {
  events: CapturedEvent[];
  duration: number;
  currentTime: number;
  onSeek: (time: number) => void;
}

const EVENT_COLORS: Record<string, string> = {
  Results: "bg-blue-500",
  TurnInfo: "bg-green-500",
  AddTranscript: "bg-purple-500",
  Connected: "bg-yellow-500",
  RecognitionStarted: "bg-yellow-500",
  EndOfTranscript: "bg-red-500",
  Metadata: "bg-gray-500",
  Error: "bg-red-600",
  default: "bg-zinc-400",
};

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export default function EventTimeline({
  events,
  duration,
  currentTime,
  onSeek,
}: EventTimelineProps) {
  const timeMarkers = useMemo(() => {
    const markers: number[] = [];
    const interval = Math.ceil(duration / 6);
    for (let i = 0; i <= duration; i += interval) {
      markers.push(i);
    }
    if (markers[markers.length - 1] !== Math.floor(duration)) {
      markers.push(Math.floor(duration));
    }
    return markers;
  }, [duration]);

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const percent = x / rect.width;
    const time = percent * duration;
    onSeek(Math.max(0, Math.min(time, duration)));
  };

  const progressPercent = (currentTime / duration) * 100;

  return (
    <div className="bg-zinc-800 rounded-lg p-4 space-y-2">
      <div className="text-sm text-zinc-400 font-medium">Event Timeline</div>

      <div
        className="relative h-16 bg-zinc-900 rounded cursor-pointer overflow-hidden"
        onClick={handleClick}
      >
        {/* Progress indicator */}
        <div
          className="absolute top-0 bottom-0 bg-zinc-700/50 pointer-events-none"
          style={{ width: `${Math.min(progressPercent, 100)}%` }}
        />

        {/* Current time indicator */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-white z-10 pointer-events-none"
          style={{ left: `${Math.min(progressPercent, 100)}%` }}
        />

        {/* Event markers */}
        <div className="absolute inset-0 flex items-center">
          {events.map((event, index) => {
            const leftPercent = Math.min((event.timestamp / duration) * 100, 100);
            const colorClass =
              EVENT_COLORS[event.event_type] || EVENT_COLORS.default;

            return (
              <div
                key={index}
                className={`absolute w-2 h-8 rounded-sm ${colorClass} opacity-80 hover:opacity-100 transition-opacity`}
                style={{ left: `${leftPercent}%`, transform: "translateX(-50%)" }}
                title={`${formatTime(event.timestamp)} - ${event.event_type}`}
              />
            );
          })}
        </div>
      </div>

      {/* Time markers */}
      <div className="flex justify-between text-xs text-zinc-500">
        {timeMarkers.map((time, index) => (
          <span key={index}>{formatTime(time)}</span>
        ))}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 pt-2">
        {Object.entries(EVENT_COLORS)
          .filter(([key]) => key !== "default")
          .slice(0, 6)
          .map(([eventType, colorClass]) => (
            <div key={eventType} className="flex items-center gap-1 text-xs text-zinc-400">
              <div className={`w-2 h-2 rounded-sm ${colorClass}`} />
              <span>{eventType}</span>
            </div>
          ))}
      </div>
    </div>
  );
}
