
import json
import mimetypes
from pathlib import Path

import requests

from config import STT_API, REQUEST_TIMEOUT
from utils import json_to_txt


def transcribe_audio(audio_path):
    """
    Gửi audio đến STT API.

    Returns:
        tuple: (output_json, output_txt)
    """
    audio_path = Path(audio_path)

    mime_type = (
        mimetypes.guess_type(audio_path.name)[0]
        or "application/octet-stream"
    )

    with audio_path.open("rb") as audio:
        response = requests.post(
            STT_API,
            files={
                "file": (
                    audio_path.name,
                    audio,
                    mime_type
                )
            },
            timeout=REQUEST_TIMEOUT,
        )

    response.raise_for_status()

    try:
        result = response.json()
    except ValueError:
        raise ValueError("STT API không trả về JSON hợp lệ.")

    output_dir = Path(__file__).resolve().parents[2] / "AudioOutput"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_json = output_dir / (
        audio_path.stem + "_output_diarization_whisper.json"
    )

    output_txt = output_dir / (
        audio_path.stem + "_output_diarization_whisper.txt"
    )

    with output_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if not isinstance(result, dict) or "segments" not in result:
        raise ValueError(
            f"JSON không có trường 'segments'. "
            f"JSON đã lưu tại: {output_json}"
        )

    json_to_txt(result, output_txt)

    return output_json, output_txt
