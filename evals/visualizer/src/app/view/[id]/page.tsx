"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { EventCaptureResult } from "@/types";
import AudioPlayer from "@/components/AudioPlayer";
import EventTimeline from "@/components/EventTimeline";
import EventList from "@/components/EventList";

const PROVIDER_COLORS: Record<string, string> = {
  deepgram: "bg-blue-500/20 text-blue-300",
  "deepgram-flux": "bg-green-500/20 text-green-300",
  speechmatics: "bg-purple-500/20 text-purple-300",
};

export default function ViewPage() {
  const params = useParams();
  const id = params.id as string;

  const [result, setResult] = useState<EventCaptureResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    async function fetchResult() {
      try {
        const response = await fetch(`/api/results/${id}`);
        if (!response.ok) {
          if (response.status === 404) {
            throw new Error("Result not found");
          }
          throw new Error("Failed to fetch result");
        }
        const data = await response.json();
        setResult(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }

    if (id) {
      fetchResult();
    }
  }, [id]);

  const handleTimeUpdate = useCallback((time: number) => {
    setCurrentTime(time);
  }, []);

  const handlePlayingChange = useCallback((playing: boolean) => {
    setIsPlaying(playing);
  }, []);

  const handleSeek = useCallback((time: number) => {
    setCurrentTime(time);
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-950 text-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-zinc-950 text-white p-6">
        <div className="max-w-4xl mx-auto">
          <Link href="/" className="text-zinc-400 hover:text-white mb-4 inline-block">
            &larr; Back to results
          </Link>
          <div className="bg-red-500/20 text-red-300 p-4 rounded-lg">{error}</div>
        </div>
      </div>
    );
  }

  if (!result) {
    return null;
  }

  const audioUrl = `/api/audio/${result.audio_file}`;

  return (
    <div className="min-h-screen bg-zinc-950 text-white">
      <div className="max-w-7xl mx-auto px-6 py-6">
        {/* Header */}
        <header className="mb-6">
          <Link href="/" className="text-zinc-400 hover:text-white mb-2 inline-block text-sm">
            &larr; Back to results
          </Link>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{result.audio_file}</h1>
            <span
              className={`text-sm px-2 py-0.5 rounded ${
                PROVIDER_COLORS[result.provider] || "bg-zinc-700 text-zinc-300"
              }`}
            >
              {result.provider}
            </span>
            {result.keyterms && result.keyterms.length > 0 && (
              <span className="text-sm px-2 py-0.5 rounded bg-amber-500/20 text-amber-300">
                {result.keyterms.length} keyterm{result.keyterms.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          {result.keyterms && result.keyterms.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {result.keyterms.map((term, index) => (
                <span
                  key={index}
                  className="text-xs px-2 py-1 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30"
                >
                  {term}
                </span>
              ))}
            </div>
          )}
          {result.transcript && (
            <p className="text-zinc-400 mt-2 text-sm line-clamp-2">
              {result.transcript}
            </p>
          )}
        </header>

        {/* Main content */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left column: Audio player and timeline */}
          <div className="lg:col-span-2 space-y-4">
            <AudioPlayer
              audioUrl={audioUrl}
              duration={result.duration}
              currentTime={currentTime}
              onTimeUpdate={handleTimeUpdate}
              onPlayingChange={handlePlayingChange}
            />

            <EventTimeline
              events={result.events}
              duration={result.duration}
              currentTime={currentTime}
              onSeek={handleSeek}
            />

            {/* Transcript section */}
            {result.transcript && (
              <div className="bg-zinc-800 rounded-lg p-4">
                <div className="text-sm text-zinc-400 font-medium mb-2">
                  Final Transcript
                </div>
                <p className="text-zinc-300">{result.transcript}</p>
              </div>
            )}
          </div>

          {/* Right column: Event list */}
          <div className="lg:col-span-1 h-[calc(100vh-12rem)]">
            <EventList
              events={result.events}
              currentTime={currentTime}
              onSeek={handleSeek}
              provider={result.provider}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
