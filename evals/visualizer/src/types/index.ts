export interface CapturedEvent {
  timestamp: number;
  event_type: string;
  data: Record<string, unknown>;
}

export interface EventCaptureResult {
  audio_file: string;
  audio_path: string;
  provider: string;
  duration: number;
  created_at: string;
  events: CapturedEvent[];
  transcript: string;
}

export interface ResultSummary {
  id: string;
  audio_file: string;
  provider: string;
  duration: number;
  created_at: string;
  event_count: number;
}
