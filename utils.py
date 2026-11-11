import json


def json_to_txt(data, output_path):
    """Chuyển JSON có danh sách segments thành file TXT kèm thời gian và speaker."""

    if not isinstance(data, dict):
        raise ValueError("Dữ liệu JSON phải là object.")

    segments = data.get("segments")

    if not isinstance(segments, list):
        raise ValueError("JSON không có danh sách 'segments' hợp lệ.")

    def format_time(seconds):
        minutes = int(seconds // 60)
        seconds = seconds % 60
        return f"{minutes:02d}:{seconds:05.2f}"

    with open(output_path, "w", encoding="utf-8") as f:
        for segment in segments:
            if not isinstance(segment, dict):
                continue

            start = segment.get("start", 0)
            end = segment.get("end", 0)
            speaker = segment.get("speaker", "Unknown")
            text = segment.get("text", "").strip()

            if text:
                start_time = format_time(float(start))
                end_time = format_time(float(end))

                f.write(
                    f"{start_time} - {end_time} - {speaker}: {text}\n"
                )

def response_to_text(response):
    """Chuyển HTTP response thành chuỗi văn bản."""

    content_type = response.headers.get("Content-Type", "").lower()

    if "json" in content_type:
        try:
            data = response.json()

            if isinstance(data, str):
                return data

            if isinstance(data, dict):
                for key in ("summary", "answer", "text", "result", "response"):
                    value = data.get(key)

                    if isinstance(value, str):
                        return value

            return json.dumps(data, ensure_ascii=False, indent=2)

        except ValueError:
            pass

    return response.text


def format_request_error(error):
    """Chuyển lỗi HTTP request thành thông báo dễ đọc."""

    import requests

    if isinstance(error, requests.exceptions.Timeout):
        return "API xử lý quá lâu (timeout)."

    if isinstance(error, requests.exceptions.ConnectionError):
        return "Không kết nối được API. Kiểm tra server và mạng."

    if isinstance(error, requests.exceptions.HTTPError):
        response = error.response

        if response is not None:
            body = response.text[:1500]

            return (
                f"Lỗi HTTP: {error}\n"
                f"{body}"
            )

        return f"Lỗi HTTP: {error}"

    if isinstance(error, requests.exceptions.RequestException):
        return f"Lỗi khi gửi request: {error}"

    return str(error)