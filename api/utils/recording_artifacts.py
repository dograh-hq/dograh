from typing import Literal

RecordingTrack = Literal["mixed", "user", "bot"]


def get_recording_storage_key(extra: dict | None, track: RecordingTrack) -> str | None:
    recordings = (extra or {}).get("recordings", {})
    if not isinstance(recordings, dict):
        return None

    artifact = recordings.get(track)
    if isinstance(artifact, str):
        return artifact
    if isinstance(artifact, dict):
        storage_key = artifact.get("storage_key")
        return storage_key if isinstance(storage_key, str) else None
    return None


def get_recording_storage_backend(
    extra: dict | None, track: RecordingTrack
) -> str | None:
    recordings = (extra or {}).get("recordings", {})
    if not isinstance(recordings, dict):
        return None

    artifact = recordings.get(track)
    if isinstance(artifact, dict):
        storage_backend = artifact.get("storage_backend")
        return storage_backend if isinstance(storage_backend, str) else None
    return None


def has_recording_track(extra: dict | None, track: RecordingTrack) -> bool:
    return bool(get_recording_storage_key(extra, track))
