import type { CreateSessionResponse } from "@/client";

export type SakinahSession = CreateSessionResponse & { scenario: string };

export type { TranscriptTurn } from "@/client";

