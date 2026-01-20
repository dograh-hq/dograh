"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ResultSummary } from "@/types";

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatDate(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const PROVIDER_COLORS: Record<string, string> = {
  deepgram: "bg-blue-500/20 text-blue-300",
  "deepgram-flux": "bg-green-500/20 text-green-300",
  speechmatics: "bg-purple-500/20 text-purple-300",
};

export default function Home() {
  const [results, setResults] = useState<ResultSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchResults() {
      try {
        const response = await fetch("/api/results");
        if (!response.ok) {
          throw new Error("Failed to fetch results");
        }
        const data = await response.json();
        setResults(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }

    fetchResults();
  }, []);

  return (
    <div className="min-h-screen bg-zinc-950 text-white">
      <div className="max-w-4xl mx-auto px-6 py-12">
        <header className="mb-12">
          <h1 className="text-3xl font-bold">STT Event Visualizer</h1>
          <p className="text-zinc-400 mt-2">
            Visualize captured WebSocket events from STT providers
          </p>
        </header>

        {loading && (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div>
          </div>
        )}

        {error && (
          <div className="bg-red-500/20 text-red-300 p-4 rounded-lg">
            {error}
          </div>
        )}

        {!loading && !error && results.length === 0 && (
          <div className="text-center py-12 text-zinc-500">
            <p className="text-lg mb-4">No results found</p>
            <p className="text-sm">
              Run the event capture script to generate results:
            </p>
            <code className="block mt-2 bg-zinc-800 p-3 rounded text-zinc-300 text-sm">
              python -m evals.stt.event_capture audio/multi_speaker.m4a --provider deepgram
            </code>
          </div>
        )}

        {!loading && !error && results.length > 0 && (
          <div className="space-y-3">
            {results.map((result) => (
              <Link
                key={result.id}
                href={`/view/${result.id}`}
                className="block bg-zinc-900 hover:bg-zinc-800 rounded-lg p-4 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-3">
                      <span className="font-medium">{result.audio_file}</span>
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          PROVIDER_COLORS[result.provider] ||
                          "bg-zinc-700 text-zinc-300"
                        }`}
                      >
                        {result.provider}
                      </span>
                    </div>
                    <div className="text-sm text-zinc-500">
                      {formatDate(result.created_at)}
                    </div>
                  </div>
                  <div className="text-right space-y-1">
                    <div className="text-sm text-zinc-400">
                      {formatDuration(result.duration)}
                    </div>
                    <div className="text-xs text-zinc-500">
                      {result.event_count} events
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
