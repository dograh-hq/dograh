import type { VoiceInfo } from "@/client/types.gen";

const SARVAM_LANG_NAMES: Record<string, [string, string]> = {
    as: ["Assamese", "as"],
    bn: ["Bengali", "bn"],
    en: ["English", "en"],
    enhi: ["English-Hindi", "en-hi"],
    gu: ["Gujarati", "gu"],
    hi: ["Hindi", "hi"],
    kn: ["Kannada", "kn"],
    mr: ["Marathi", "mr"],
    pa: ["Punjabi", "pa"],
    ta: ["Tamil", "ta"],
    te: ["Telugu", "te"],
};

function capitalize(value: string): string {
    return value.charAt(0).toUpperCase() + value.slice(1);
}

function sarvamStyleName(styleKey: string): string {
    if (styleKey.toLowerCase() === "kyc") return "KYC";
    return styleKey.replaceAll("_", " ").split(" ").map(capitalize).join(" ");
}

/**
 * For providers with a static voice list (like Sarvam Bulbul), parse the schema
 * options directly on the client to provide 0 ms instant loading, rich metadata,
 * and search without unnecessary network calls.
 */
export function getStaticVoices(provider: string, options?: string[], model?: string): VoiceInfo[] | undefined {
    if (provider !== "sarvam" || !options?.length) return undefined;

    if (model === "bulbul:v3") {
        return options.map((name) => ({
            voice_id: name,
            name: capitalize(name),
            description: "Multilingual",
            language: "multilingual",
        }));
    }

    return options.map((voiceId) => {
        const parts = voiceId.split("_");
        if (parts.length >= 3) {
            const rawName = parts[0];
            const langCode = parts[1];
            const styleKey = parts.slice(2).join("_");
            const [langName, langTag] = SARVAM_LANG_NAMES[langCode] || [capitalize(langCode), langCode];
            const styleName = sarvamStyleName(styleKey);
            const speakerName = capitalize(rawName);
            return {
                voice_id: voiceId,
                name: speakerName,
                description: `${langName} • ${styleName} • ${voiceId}`,
                accent: styleName,
                language: langTag,
            };
        }
        return {
            voice_id: voiceId,
            name: capitalize(voiceId.replaceAll("_", " ")),
            description: voiceId,
        };
    });
}
