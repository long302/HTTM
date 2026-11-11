
import requests
from pathlib import Path
import json

def json_to_txt(input_path, output_path):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(output_path, "w", encoding="utf-8") as f:
        for segment in data.get("segments", []):
            speaker = segment.get("speaker", "Unknown")
            text = segment.get("text", "").strip()

            if text:
                f.write(f"{speaker}: {text}\n")


STT_URL = "http://172.11.53.189:8000/api/transcribe"#in: mp3/wav, out: json -> STT
SUMMARY_URL ="http://172.11.53.189:8000/api/transcribe"#in: txt, out: txt -> summary text
RAG_URL ="http://172.11.53.189:8000/api/transcribe" #in: txt, out: txt -> Give answer of user
AUDIO_PATH = Path("Test.mp3") # thay đổi bằng cách tự chọn mp3/wav từ người dùng

if not AUDIO_PATH.is_file():
    print(f"Khong tim thay file: {AUDIO_PATH}")
    raise SystemExit(1)

try:
    with AUDIO_PATH.open("rb") as audio:
        response = requests.post(
            API_URL,
            files={
                "file": (
                    AUDIO_PATH.name,
                    audio,
                    "audio/mpeg"
                )
            },
            timeout=600000
        )

    response.raise_for_status()
    result = response.json()

    print("Ket qua:")
    print(result)

    with open("output.txt", "w", encoding="utf-8") as f:
        f.write(response.text)

    json_to_txt("output.json", "output.txt")
    print("Da luu ket qua vao output.txt")

except requests.exceptions.ConnectionError:
    print("Khong the ket noi API. Kiem tra server va Wi-Fi.")

except requests.exceptions.Timeout:
    print("API xu ly qua lau, da het thoi gian cho.")

except requests.exceptions.HTTPError as e:
    print("Loi HTTP:", e)
    print(response.text)

except requests.exceptions.RequestException as e:
    print("Loi request:", e)
