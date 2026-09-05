import { client } from "@/client/client.gen";

export interface CallReplay {
    call_id: string;
    agent_run_id: number;
    recording_signed_url: string | null;
    expires_in: number;
    transcript: string | null;
    utterances: Array<{
        id: string;
        speaker: string;
        sequence_number: number;
        start_ms: number | null;
        end_ms: number | null;
        transcript: string;
    }>;
}

export async function getCallReplay(callId: string): Promise<CallReplay> {
    const response = await client.get<{ 200: CallReplay }>({
        url: "/api/v1/call-history/{call_id}/replay",
        path: { call_id: callId },
        query: { expires_in: 300 },
    });
    if (response.error || !response.data) {
        throw new Error("Unable to load call replay.");
    }
    return response.data;
}
