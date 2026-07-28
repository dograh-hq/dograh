import type {
    AmbientNoiseConfigurationDefaults,
    OrganizationAiModelConfigurationV2,
    WorkflowConfigurationDefaults as GeneratedWorkflowConfigurationDefaults,
} from "@/client/types.gen";

export type WorkflowConfigurationDefaults = GeneratedWorkflowConfigurationDefaults;

export type AmbientNoiseConfiguration = Omit<
    AmbientNoiseConfigurationDefaults,
    "enabled" | "volume"
> & {
    enabled: boolean;
    volume: number;
    storage_key?: string;
    storage_backend?: string;
    original_filename?: string;
};

export type TurnStopStrategy = NonNullable<GeneratedWorkflowConfigurationDefaults["turn_stop_strategy"]>;
export type TurnStartStrategy = NonNullable<GeneratedWorkflowConfigurationDefaults["turn_start_strategy"]>;
export const DEFAULT_TURN_START_MIN_WORDS = 3;
export const DEFAULT_PROVISIONAL_VAD_PAUSE_SECS = 1.5;

export const TURN_START_STRATEGY_OPTIONS: Array<{
    value: TurnStartStrategy;
    label: string;
    description: string;
}> = [
    {
        value: 'default',
        label: 'Default',
        description: 'Use the platform default: external STT turn signals when available, otherwise local VAD.',
    },
    {
        value: 'min_words',
        label: 'Minimum words',
        description: 'Wait for a minimum number of transcribed words before interrupting bot speech.',
    },
    {
        value: 'provisional_vad',
        label: 'Provisional VAD',
        description: 'Pause bot audio on voice activity, then confirm the interruption with transcription.',
    },
];

export interface VoicemailDetectionConfiguration {
    enabled: boolean;
    use_workflow_llm: boolean;
    provider?: string;
    model?: string;
    api_key?: string;
    system_prompt?: string;
    long_speech_timeout: number;  // seconds cutoff for long speech detection
}

export const DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION: VoicemailDetectionConfiguration = {
    enabled: false,
    use_workflow_llm: true,
    long_speech_timeout: 8.0,
};

export interface TranscriptConfiguration {
    include_end_timestamps: boolean;
}

export interface ExternalPBXFieldMapping {
    context_path: string;
    destination_field: string;
}

export const DEFAULT_TRANSCRIPT_CONFIGURATION: TranscriptConfiguration = {
    include_end_timestamps: false,
};

export interface ModelOverrides {
    llm?: {
        provider?: string;
        model?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    tts?: {
        provider?: string;
        model?: string;
        voice?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    stt?: {
        provider?: string;
        model?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    realtime?: {
        provider?: string;
        model?: string;
        voice?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    is_realtime?: boolean;
}

type WorkflowConfigurationBase = Omit<
    GeneratedWorkflowConfigurationDefaults,
    | "ambient_noise_configuration"
    | "max_call_duration"
    | "max_user_idle_timeout"
    | "smart_turn_stop_secs"
    | "turn_start_strategy"
    | "turn_start_min_words"
    | "provisional_vad_pause_secs"
    | "turn_stop_strategy"
    | "dictionary"
    | "context_compaction_enabled"
    | "external_pbx_field_mappings"
>;

export type WorkflowConfigurations = WorkflowConfigurationBase & {
    ambient_noise_configuration: AmbientNoiseConfiguration;
    max_call_duration: number;  // Maximum call duration in seconds
    max_user_idle_timeout: number;  // Maximum user idle time in seconds
    smart_turn_stop_secs: number;  // Timeout in seconds for incomplete turn detection
    turn_start_strategy: TurnStartStrategy;  // Strategy for detecting start of user turn/interruption
    turn_start_min_words: number;  // Minimum transcribed words required for minimum-word interruptions
    provisional_vad_pause_secs: number;  // Seconds to pause bot output while awaiting transcript confirmation
    turn_stop_strategy: TurnStopStrategy;  // Strategy for detecting end of user turn
    dictionary?: string;  // Comma-separated words for voice agent to listen for
    voicemail_detection?: VoicemailDetectionConfiguration;
    transcript_configuration: TranscriptConfiguration;
    context_compaction_enabled: boolean;  // Summarize context on node transitions to remove stale tool calls
    external_pbx_field_mappings: ExternalPBXFieldMapping[];
    model_overrides?: ModelOverrides;  // Per-workflow model configuration overrides
    model_configuration_v2_override?: OrganizationAiModelConfigurationV2;  // Full v2 model configuration override
    [key: string]: unknown;  // Allow additional properties for future configurations
};

const FALLBACK_WORKFLOW_CONFIGURATIONS: WorkflowConfigurations = {
    ambient_noise_configuration: {
        enabled: false,
        volume: 0.3
    },
    max_call_duration: 300,
    max_user_idle_timeout: 10,  // 10 seconds
    smart_turn_stop_secs: 2,  // 2 seconds
    turn_start_strategy: 'default',  // Default to platform-chosen user turn start detection
    turn_start_min_words: DEFAULT_TURN_START_MIN_WORDS,
    provisional_vad_pause_secs: DEFAULT_PROVISIONAL_VAD_PAUSE_SECS,
    turn_stop_strategy: 'transcription',  // Default to transcription-based detection
    dictionary: '',
    transcript_configuration: DEFAULT_TRANSCRIPT_CONFIGURATION,
    context_compaction_enabled: false,
    external_pbx_field_mappings: [],
};

export function resolveWorkflowConfigurations(
    configurations?: Partial<WorkflowConfigurations> | null,
    defaults?: WorkflowConfigurationDefaults | null,
): WorkflowConfigurations {
    return {
        ...FALLBACK_WORKFLOW_CONFIGURATIONS,
        ...defaults,
        ...configurations,
        ambient_noise_configuration: {
            ...FALLBACK_WORKFLOW_CONFIGURATIONS.ambient_noise_configuration,
            ...defaults?.ambient_noise_configuration,
            ...configurations?.ambient_noise_configuration,
        },
        max_call_duration:
            configurations?.max_call_duration
            ?? defaults?.max_call_duration
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.max_call_duration,
        max_user_idle_timeout:
            configurations?.max_user_idle_timeout
            ?? defaults?.max_user_idle_timeout
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.max_user_idle_timeout,
        smart_turn_stop_secs:
            configurations?.smart_turn_stop_secs
            ?? defaults?.smart_turn_stop_secs
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.smart_turn_stop_secs,
        turn_start_strategy:
            configurations?.turn_start_strategy
            ?? defaults?.turn_start_strategy
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.turn_start_strategy,
        turn_start_min_words:
            configurations?.turn_start_min_words
            ?? defaults?.turn_start_min_words
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.turn_start_min_words,
        provisional_vad_pause_secs:
            configurations?.provisional_vad_pause_secs
            ?? defaults?.provisional_vad_pause_secs
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.provisional_vad_pause_secs,
        turn_stop_strategy:
            configurations?.turn_stop_strategy
            ?? defaults?.turn_stop_strategy
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.turn_stop_strategy,
        dictionary:
            configurations?.dictionary
            ?? defaults?.dictionary
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.dictionary,
        context_compaction_enabled:
            configurations?.context_compaction_enabled
            ?? defaults?.context_compaction_enabled
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.context_compaction_enabled,
        external_pbx_field_mappings:
            configurations?.external_pbx_field_mappings
            ?? defaults?.external_pbx_field_mappings
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.external_pbx_field_mappings,
        transcript_configuration: {
            ...DEFAULT_TRANSCRIPT_CONFIGURATION,
            ...(defaults?.transcript_configuration as Partial<TranscriptConfiguration> | undefined),
            ...(configurations?.transcript_configuration as Partial<TranscriptConfiguration> | undefined),
        },
    };
}
