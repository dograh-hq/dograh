"""Audio utilities that work on uploaded files."""

import re
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.pipecat.noise_reduction.file_denoise import (
    MAX_SAMPLE_RATE,
    MAX_UPLOAD_BYTES,
    MIN_SAMPLE_RATE,
    AudioDecodeError,
    AudioTooLongError,
    DenoiseMode,
    OutputFormat,
    denoise_audio_file,
)

router = APIRouter(prefix="/audio", tags=["audio"])


@router.post(
    "/denoise",
    summary="Remove background noise from an audio file",
    response_class=Response,
    responses={
        200: {
            "description": "The denoised audio, mono.",
            "content": {"audio/wav": {}, "audio/mpeg": {}, "audio/flac": {}},
        },
        400: {"description": "The file is not decodable audio."},
        413: {"description": "The file is too large or too long."},
    },
)
async def denoise_audio(
    user: Annotated[UserModel, Depends(get_user)],
    file: Annotated[UploadFile, File(description="Audio in any common format.")],
    mode: Annotated[
        DenoiseMode,
        Form(
            description=(
                "`speech`: what a live agent's STT would receive, the user's "
                "denoised speech with silence wherever VAD hears no speech. "
                "`full`: denoise the whole file."
            )
        ),
    ] = "speech",
    output_format: Annotated[OutputFormat, Form()] = "wav",
    sample_rate: Annotated[
        int | None,
        Form(
            ge=MIN_SAMPLE_RATE,
            le=MAX_SAMPLE_RATE,
            description=(
                "Output rate in Hz. Defaults to 16000 in `speech` mode (8000 "
                "simulates a phone line) and to the input's rate in `full` mode."
            ),
        ),
    ] = None,
) -> Response:
    """Upload an audio file and get back the same audio with background noise removed.

    In `speech` mode (default) the file is replayed through the same speech
    gate a live call would use: Silero VAD with the agent's settings decides
    when the user is speaking, that speech is denoised, and everything else is
    silence. The output is mono and lines up sample for sample with the input.
    """
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    if not data:
        raise HTTPException(status_code=400, detail="File is empty")

    try:
        result = await denoise_audio_file(data, output_format, sample_rate)
    except AudioTooLongError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except AudioDecodeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # The client's filename goes into a response header; keep it to safe characters.
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", PurePath(file.filename or "").stem)[:100]
    filename = f"{stem or 'audio'}-denoised.{output_format}"
    return Response(
        content=result.audio,
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Audio-Duration-Seconds": f"{result.duration_secs:.3f}",
            "X-Audio-Sample-Rate": str(result.sample_rate),
            "X-Processing-Time-Ms": f"{result.processing_ms:.0f}",
            **(
                {
                    "X-Speech-Segments": str(result.speech_segments),
                    "X-Speech-Seconds": f"{result.speech_secs:.3f}",
                }
                if result.speech_segments is not None
                else {}
            ),
        },
    )
