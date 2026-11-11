
from pathlib import Path

import requests

from config import SUMMARY_API, ASK_API, REQUEST_TIMEOUT
from utils import response_to_text


def summarize_file(txt_path):
    """
    Đọc file TXT, gửi đến Summary API và lưu kết quả.

    Returns:
        tuple[Path, str]: Đường dẫn file summary và nội dung.
    """

    txt_path = Path(txt_path)

    if not txt_path.is_file():
        raise FileNotFoundError(
            f"Không tìm thấy file TXT: {txt_path}"
        )

    content = txt_path.read_text(encoding="utf-8")

    if not content.strip():
        raise ValueError("File TXT đang trống.")

    # Gửi nội dung TXT dưới dạng multipart/form-data.
    response = requests.post(
        SUMMARY_API,
        files={
            "file": (
                txt_path.name,
                content.encode("utf-8"),
                "text/plain; charset=utf-8",
            )
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    answer = response_to_text(response)

    # Lưu summary riêng khỏi file TXT đầu vào.
    output_dir = Path(__file__).resolve().parents[2] / "SummaryOutput"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / (txt_path.stem + "_summary.txt")

    output_path.write_text(
        answer,
        encoding="utf-8",
    )

    return output_path, answer


def ask_question(meeting_id, question):
    """
    Gửi meeting_id và question đến Ask API.

    Returns:
        str: Câu trả lời.
    """

    if not meeting_id:
        raise ValueError("meeting_id không được để trống.")

    if not question.strip():
        raise ValueError("Câu hỏi không được để trống.")

    response = requests.post(
        ASK_API,
        json={
            "meeting_id": meeting_id,
            "question": question,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    try:
        result = response.json()
    except ValueError as e:
        raise ValueError(
            "Ask API không trả về JSON hợp lệ."
        ) from e

    if isinstance(result, dict):
        answer = result.get("answer")

        if answer is not None:
            if isinstance(answer, str):
                return answer

            return str(answer)

    return str(result)
