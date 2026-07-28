export function createUuid(): string {
    const webCrypto = globalThis.crypto;

    if (typeof webCrypto?.randomUUID === "function") {
        return webCrypto.randomUUID();
    }

    if (typeof webCrypto?.getRandomValues !== "function") {
        throw new Error("Secure random number generation is unavailable");
    }

    const bytes = webCrypto.getRandomValues(new Uint8Array(16));

    // Mark the generated value as an RFC 4122 version 4 UUID.
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
    return [
        hex.slice(0, 4).join(""),
        hex.slice(4, 6).join(""),
        hex.slice(6, 8).join(""),
        hex.slice(8, 10).join(""),
        hex.slice(10, 16).join(""),
    ].join("-");
}
