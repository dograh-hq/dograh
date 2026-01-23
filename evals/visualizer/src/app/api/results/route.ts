import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { ResultSummary, EventCaptureResult } from "@/types";

const RESULTS_DIR = path.join(process.cwd(), "..", "stt", "results");

export async function GET() {
  try {
    if (!fs.existsSync(RESULTS_DIR)) {
      return NextResponse.json([]);
    }

    const files = fs.readdirSync(RESULTS_DIR).filter((f) => f.endsWith(".json"));
    const results: ResultSummary[] = [];

    for (const file of files) {
      try {
        const filePath = path.join(RESULTS_DIR, file);
        const content = fs.readFileSync(filePath, "utf-8");
        const data: EventCaptureResult = JSON.parse(content);

        results.push({
          id: file.replace(".json", ""),
          audio_file: data.audio_file,
          provider: data.provider,
          duration: data.duration,
          created_at: data.created_at,
          event_count: data.events.length,
        });
      } catch {
        console.error(`Failed to parse ${file}`);
      }
    }

    // Sort by created_at descending
    results.sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );

    return NextResponse.json(results);
  } catch (error) {
    console.error("Error reading results:", error);
    return NextResponse.json({ error: "Failed to read results" }, { status: 500 });
  }
}
