import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

const AUDIO_DIR = path.join(process.cwd(), "..", "stt", "audio");

const MIME_TYPES: Record<string, string> = {
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".m4a": "audio/mp4",
  ".ogg": "audio/ogg",
  ".webm": "audio/webm",
};

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ filename: string }> }
) {
  try {
    const { filename } = await params;
    const filePath = path.join(AUDIO_DIR, filename);

    if (!fs.existsSync(filePath)) {
      return NextResponse.json({ error: "Audio file not found" }, { status: 404 });
    }

    const ext = path.extname(filename).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";

    const fileBuffer = fs.readFileSync(filePath);

    return new NextResponse(fileBuffer, {
      headers: {
        "Content-Type": contentType,
        "Content-Length": fileBuffer.length.toString(),
      },
    });
  } catch (error) {
    console.error("Error serving audio:", error);
    return NextResponse.json({ error: "Failed to serve audio" }, { status: 500 });
  }
}
