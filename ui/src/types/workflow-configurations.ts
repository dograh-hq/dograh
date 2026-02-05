export interface VADConfiguration {
    confidence: number;
    start_seconds: number;
    stop_seconds: number;
    minimum_volume: number;
}

export interface AmbientNoiseConfiguration {
    enabled: boolean;
    volume: number;
}

export type TurnStopStrategy = 'transcription' | 'turn_analyzer';

export interface WorkflowConfigurations {
    vad_configuration?: VADConfiguration;
    ambient_noise_configuration: AmbientNoiseConfiguration;
    max_call_duration: number;  // Maximum call duration in seconds
    max_user_idle_timeout: number;  // Maximum user idle time in seconds
    smart_turn_stop_secs: number;  // Timeout in seconds for incomplete turn detection
    turn_stop_strategy: TurnStopStrategy;  // Strategy for detecting end of user turn
    dictionary?: string;  // Comma-separated words for voice agent to listen for
    [key: string]: unknown;  // Allow additional properties for future configurations
}

export const DEFAULT_WORKFLOW_CONFIGURATIONS: WorkflowConfigurations = {
    ambient_noise_configuration: {
        enabled: false,
        volume: 0.3
    },
    max_call_duration: 600,  // 10 minutes
    max_user_idle_timeout: 10,  // 10 seconds
    smart_turn_stop_secs: 2,  // 2 seconds
    turn_stop_strategy: 'transcription',  // Default to transcription-based detection
    dictionary: ''
};
