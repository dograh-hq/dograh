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

export interface WorkflowConfigurations {
    vad_configuration: VADConfiguration;
    ambient_noise_configuration: AmbientNoiseConfiguration;
    max_call_duration: number;  // Maximum call duration in seconds
    max_user_idle_timeout: number;  // Maximum user idle time in seconds
    dictionary?: string;  // Comma-separated words for voice agent to listen for
    [key: string]: unknown;  // Allow additional properties for future configurations
}

export const DEFAULT_WORKFLOW_CONFIGURATIONS: WorkflowConfigurations = {
    vad_configuration: {
        confidence: 0.7,
        start_seconds: 0.4,
        stop_seconds: 0.8,
        minimum_volume: 0.6
    },
    ambient_noise_configuration: {
        enabled: false,
        volume: 0.3
    },
    max_call_duration: 600,  // 10 minutes
    max_user_idle_timeout: 10,  // 10 seconds
    dictionary: ''
};
