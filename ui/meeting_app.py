import json
import os
import queue
import re
import tempfile
import threading
import tkinter as tk
import time
import wave
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import requests

from services.stt_service import transcribe_audio
from services.text_service import summarize_file, ask_question
from utils import format_request_error


class AIMeetingSummaryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Meeting Summary")
        self.root.geometry("1000x800")
        self.root.minsize(800, 650)

        self.stt_thread = None
        self.stop_event = threading.Event()

        self.stt_queue = queue.Queue()
        self.text_queue = queue.Queue()
        self.text_result_queue = queue.Queue()

        self.stt_status = tk.StringVar(value="Ready")
        self.summary_status = tk.StringVar(value="Ready")
        self.ask_status = tk.StringVar(value="Ready")
        self.audio_path = tk.StringVar(value="Chưa chọn file audio")
        self.txt_path = tk.StringVar(value="Chưa chọn file TXT")
        self.player_status = tk.StringVar(value="Audio chưa phát")

        # Audio player
        self.audio_player_ready = False
        self.player_duration = 0.0
        self.player_position = 0.0
        self.is_seeking = False
        self.is_playing = False
        self.is_paused = False
        self.seek_offset = 0.0
        self.playback_started_at = None
        # Các đoạn transcript có timestamp để đồng bộ với thời gian phát.
        self.transcript_segments = []
        self.active_transcript_segment = None
        self.current_caption = tk.StringVar(value="")
        self.recording_stream = None
        self.recording_chunks = []
        self.recording_lock = threading.Lock()
        self.recording_active = False

        try:
            import pygame

            pygame.mixer.init()
            self.pygame = pygame
            self.audio_player_ready = True

        except Exception as e:
            self.pygame = None
            print(f"Không khởi tạo được audio player: {e}")

        self._build_ui()

        self.text_thread = threading.Thread(
            target=self._text_worker,
            daemon=True,
        )
        self.text_thread.start()

        self.root.after(100, self._poll_queues)
        self.root.after(200, self._update_player_position)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # =========================================================
    # UI
    # =========================================================

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="AI Meeting Summary", font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(0, 10))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)
        self.stt_tab = ttk.Frame(self.notebook, padding=10)
        self.text_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.stt_tab, text="STT")
        self.notebook.add(self.text_tab, text="Summary & Ask")
        self._build_stt_tab(self.stt_tab)
        self._build_text_tab(self.text_tab)

    def _build_stt_tab(self, tab):
        stt_frame = ttk.LabelFrame(tab, text="Audio → TXT", padding=10)
        stt_frame.pack(fill="x", pady=(0, 8))
        audio_top = ttk.Frame(stt_frame)
        audio_top.pack(fill="x")
        ttk.Button(audio_top, text="Chọn file audio", command=self.choose_audio).pack(side="left")
        ttk.Label(audio_top, textvariable=self.stt_status, wraplength=700).pack(side="left", padx=10)
        ttk.Label(stt_frame, textvariable=self.audio_path, wraplength=900).pack(anchor="w", pady=(5, 4))

        player_frame = ttk.Frame(stt_frame)
        player_frame.pack(fill="x")
        ttk.Button(player_frame, text="▶ Play", command=self.play_audio).pack(side="left", padx=(0, 5))
        ttk.Button(player_frame, text="⏸ Pause", command=self.pause_audio).pack(side="left", padx=5)
        ttk.Button(player_frame, text="▶ Resume", command=self.resume_audio).pack(side="left", padx=5)
        ttk.Button(player_frame, text="■ Stop", command=self.stop_audio).pack(side="left", padx=5)
        ttk.Label(player_frame, textvariable=self.player_status).pack(side="left", padx=12)

        seek_frame = ttk.Frame(stt_frame)
        seek_frame.pack(fill="x", pady=(8, 0))
        self.current_time_label = ttk.Label(seek_frame, text="00:00", width=8, anchor="e")
        self.current_time_label.pack(side="left")
        self.seek_scale = ttk.Scale(seek_frame, from_=0, to=100, orient="horizontal", command=self._on_seek_change)
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=8)
        self.total_time_label = ttk.Label(seek_frame, text="00:00", width=8, anchor="w")
        self.total_time_label.pack(side="left")
        self.seek_scale.bind("<ButtonPress-1>", self._start_seeking)
        self.seek_scale.bind("<ButtonRelease-1>", self._finish_seeking)

        caption_frame = ttk.LabelFrame(stt_frame, text="Lời thoại hiện tại", padding=8)
        caption_frame.pack(fill="x", pady=(8, 0))
        self.caption_label = ttk.Label(caption_frame, textvariable=self.current_caption, font=("Segoe UI", 11, "bold"), wraplength=920, justify="left", anchor="w")
        self.caption_label.pack(fill="x")

        transcript_frame = ttk.LabelFrame(tab, text="Văn bản đã xử lý", padding=10)
        transcript_frame.pack(fill="both", expand=True)
        transcript_toolbar = ttk.Frame(transcript_frame)
        transcript_toolbar.pack(fill="x", pady=(0, 5))
        ttk.Button(transcript_toolbar, text="Mở file TXT", command=self.choose_transcript_to_read).pack(side="left")
        ttk.Button(transcript_toolbar, text="Xóa nội dung", command=self.clear_transcript).pack(side="left", padx=6)
        self.transcript_box = scrolledtext.ScrolledText(transcript_frame, height=12, wrap="word", font=("Consolas", 10), undo=False)
        self.transcript_box.pack(fill="both", expand=True)
        self.transcript_box.tag_configure("active_segment", background="#ffe08a")
        self.transcript_box.insert("1.0", "Văn bản đã xử lý sẽ hiển thị ở đây sau khi STT hoàn tất.")

    def _build_text_tab(self, tab):
        summary_frame = ttk.LabelFrame(tab, text="1. Gửi file TXT lên Summary API", padding=10)
        summary_frame.pack(fill="both", expand=True, pady=(0, 8))
        summary_toolbar = ttk.Frame(summary_frame)
        summary_toolbar.pack(fill="x")
        ttk.Button(summary_toolbar, text="Chọn TXT để tóm tắt", command=self.choose_txt).pack(side="left")
        ttk.Label(summary_toolbar, textvariable=self.summary_status, wraplength=700).pack(side="left", padx=10)
        ttk.Label(summary_frame, textvariable=self.txt_path, wraplength=900).pack(anchor="w", pady=(5, 7))

        compare_frame = ttk.Frame(summary_frame)
        compare_frame.pack(fill="both", expand=True)
        compare_frame.grid_columnconfigure(0, weight=1)
        compare_frame.grid_columnconfigure(1, weight=1)
        compare_frame.grid_rowconfigure(1, weight=1)
        ttk.Label(compare_frame, text="Văn bản gửi lên API", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 3))
        ttk.Label(compare_frame, text="Văn bản tóm tắt", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=(6, 0), pady=(0, 3))
        self.source_text_box = scrolledtext.ScrolledText(compare_frame, height=10, wrap="word", font=("Segoe UI", 10), undo=False)
        self.source_text_box.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.source_text_box.configure(state="disabled")
        summary_output_frame = ttk.Frame(compare_frame)
        summary_output_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        summary_output_frame.grid_rowconfigure(0, weight=1)
        summary_output_frame.grid_columnconfigure(0, weight=1)
        self.summary_box = scrolledtext.ScrolledText(summary_output_frame, height=10, wrap="word", font=("Segoe UI", 10), undo=False)
        self.summary_box.grid(row=0, column=0, sticky="nsew")
        self.summary_box.insert("1.0", "Bản tóm tắt sẽ hiển thị ở đây sau khi xử lý TXT hoàn tất.")

        ask_frame = ttk.LabelFrame(tab, text="2. Gửi JSON câu hỏi / ghi âm câu hỏi", padding=10)
        ask_frame.pack(fill="both", expand=True)
        ttk.Label(ask_frame, text="Câu hỏi (có thể nhập hoặc ghi âm):").pack(anchor="w")
        ask_row = ttk.Frame(ask_frame)
        ask_row.pack(fill="x", pady=(4, 5))
        self.ask_input = ttk.Entry(ask_row)
        self.ask_input.pack(side="left", fill="x", expand=True)
        self.ask_input.bind("<Return>", lambda _event: self.submit_question())
        ttk.Button(ask_row, text="Gửi câu hỏi", command=self.submit_question).pack(side="left", padx=(8, 0))
        recording_row = ttk.Frame(ask_frame)
        recording_row.pack(fill="x", pady=(2, 4))
        self.record_button = ttk.Button(recording_row, text="● Bắt đầu ghi âm", command=self.start_question_recording)
        self.record_button.pack(side="left")
        self.stop_record_button = ttk.Button(recording_row, text="■ Dừng và nhận dạng", command=self.stop_question_recording, state="disabled")
        self.stop_record_button.pack(side="left", padx=6)
        ttk.Label(recording_row, textvariable=self.ask_status).pack(side="left", padx=8)
        ttk.Label(ask_frame, text="Câu trả lời:").pack(anchor="w", pady=(5, 2))
        self.result_box = scrolledtext.ScrolledText(ask_frame, height=6, wrap="word", font=("Segoe UI", 10))
        self.result_box.pack(fill="both", expand=True)

    # =========================================================
    # AUDIO PLAYER
    # =========================================================

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            return f"{hours:02}:{minutes:02}:{seconds:02}"

        return f"{minutes:02}:{seconds:02}"

    def play_audio(self):
        path = self.audio_path.get()

        if path == "Chưa chọn file audio" or not Path(path).is_file():
            messagebox.showwarning(
                "Chưa có audio",
                "Vui lòng chọn file audio trước.",
            )
            return

        if not self.audio_player_ready:
            messagebox.showerror(
                "Thiếu thư viện",
                "Không khởi tạo được trình phát audio.\n\n"
                "Cài pygame bằng lệnh:\n"
                "pip install pygame mutagen",
            )
            return

        try:
            from mutagen import File as AudioFile

            audio = AudioFile(path)

            if audio is None or audio.info is None:
                raise ValueError("Không đọc được thời lượng file audio.")

            self.player_duration = float(audio.info.length)

            self.pygame.mixer.music.load(path)
            self.pygame.mixer.music.play()

            self.player_position = 0.0
            self.seek_offset = 0.0
            self.playback_started_at = time.monotonic()
            self.is_seeking = False
            self.is_playing = True
            self.is_paused = False

            self.seek_scale.configure(
                from_=0,
                to=max(1, self.player_duration),
            )
            self.seek_scale.set(0)

            self.current_time_label.config(text="00:00")
            self.total_time_label.config(
                text=self._format_time(self.player_duration)
            )

            self.player_status.set(
                f"Đang phát: {Path(path).name}"
            )

        except ImportError:
            messagebox.showerror(
                "Thiếu thư viện",
                "Cài thư viện bằng lệnh:\n\n"
                "pip install pygame mutagen",
            )

        except Exception as e:
            messagebox.showerror("Lỗi phát audio", str(e))

    def pause_audio(self):
        if not self.audio_player_ready or not self.is_playing:
            return

        try:
            self._sync_player_position()
            self.pygame.mixer.music.pause()

            self.is_playing = False
            self.is_paused = True
            self.playback_started_at = None

            self.player_status.set("Đã tạm dừng")

        except Exception as e:
            messagebox.showerror("Lỗi audio", str(e))

    def resume_audio(self):
        if not self.audio_player_ready or not self.is_paused:
            return

        try:
            self.pygame.mixer.music.unpause()

            self.seek_offset = self.player_position
            self.playback_started_at = time.monotonic()
            self.is_playing = True
            self.is_paused = False

            self.player_status.set("Đang phát")

        except Exception as e:
            messagebox.showerror("Lỗi audio", str(e))

    def stop_audio(self):
        if not self.audio_player_ready:
            return

        try:
            self.pygame.mixer.music.stop()

            self.is_playing = False
            self.is_paused = False
            self.is_seeking = False

            self.player_position = 0.0
            self.seek_offset = 0.0
            self.playback_started_at = None

            self.seek_scale.set(0)
            self.current_time_label.config(text="00:00")
            self._sync_transcript_to_time(0.0)
            self.player_status.set("Đã dừng")

        except Exception as e:
            messagebox.showerror("Lỗi audio", str(e))

    def _sync_player_position(self):
        if not self.audio_player_ready:
            return

        if not self.is_playing:
            return

        if self.playback_started_at is not None:
            elapsed = time.monotonic() - self.playback_started_at
            self.player_position = min(
                self.seek_offset + elapsed,
                self.player_duration,
            )

    def _start_seeking(self, _event):
        self.is_seeking = True

    def _on_seek_change(self, value):
        position = float(value)

        self.current_time_label.config(
            text=self._format_time(position)
        )

    def _finish_seeking(self, _event):
        self.is_seeking = False

        if not self.audio_player_ready or self.player_duration <= 0:
            return

        position = float(self.seek_scale.get())
        position = max(0, min(position, self.player_duration))

        try:
            path = self.audio_path.get()

            # Ghi nhận trạng thái phát trước khi tua.
            was_playing = self.is_playing
            was_paused = self.is_paused

            # Phát lại từ vị trí mới.
            self.pygame.mixer.music.stop()
            self.pygame.mixer.music.play(start=position)

            self.seek_offset = position
            self.player_position = position
            self.playback_started_at = time.monotonic() if (was_playing or was_paused) else None

            if was_paused:
                self.pygame.mixer.music.pause()
                self.is_playing = False
                self.is_paused = True
                self.playback_started_at = None
                self.player_status.set("Đã tạm dừng")

            elif was_playing:
                self.is_playing = True
                self.is_paused = False
                self.player_status.set(
                    f"Đang phát: {Path(path).name}"
                )

            else:
                # Nếu trước đó đã dừng, giữ trạng thái dừng.
                self.pygame.mixer.music.stop()
                self.is_playing = False
                self.is_paused = False
                self.playback_started_at = None

            self.current_time_label.config(
                text=self._format_time(position)
            )
            self._sync_transcript_to_time(position)

        except Exception as e:
            messagebox.showerror(
                "Lỗi tua audio",
                "Không thể tua đến vị trí đã chọn.\n\n"
                f"{e}",
            )

    def _update_player_position(self):
        if self.stop_event.is_set():
            return

        try:
            if (
                self.audio_player_ready
                and self.is_playing
                and not self.is_seeking
            ):
                self._sync_player_position()

                self.seek_scale.set(self.player_position)

                self.current_time_label.config(
                    text=self._format_time(self.player_position)
                )
                self._sync_transcript_to_time(self.player_position)

                # Kiểm tra audio đã phát xong.
                if not self.pygame.mixer.music.get_busy():
                    self.is_playing = False
                    self.is_paused = False
                    self.playback_started_at = None

                    if self.player_duration > 0:
                        self.player_position = self.player_duration
                        self.seek_scale.set(self.player_duration)

                        self.current_time_label.config(
                            text=self._format_time(self.player_duration)
                        )

                    self.player_status.set("Đã phát xong")

        except Exception:
            pass

        self.root.after(200, self._update_player_position)

    # =========================================================
    # TRANSCRIPT DISPLAY
    # =========================================================

    @staticmethod
    def _timestamp_to_seconds(value):
        """Convert SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm to seconds."""
        value = str(value).strip().replace(",", ".")
        parts = value.split(":")
        try:
            if len(parts) == 1:
                return float(parts[0])
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except (TypeError, ValueError):
            return None
        return None

    @classmethod
    def _extract_transcript_segments(cls, content):
        """Parse the app's TXT format, SRT/VTT, or a Whisper-style JSON file."""
        segments = []

        # App output format: 00:08.34 - 00:10.34 - Speaker 1: transcript text
        line_pattern = re.compile(
            r'^\s*(?P<start>\d{1,2}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?)\s*'
            r'-\s*(?P<end>\d{1,2}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?)\s*'
            r'-\s*(?P<body>.+?)\s*$'
        )
        for line_number, line in enumerate(content.splitlines(), start=1):
            match = line_pattern.match(line)
            if not match:
                continue
            start = cls._timestamp_to_seconds(match.group('start'))
            end = cls._timestamp_to_seconds(match.group('end'))
            body = match.group('body').strip()
            if start is not None and end is not None and end > start and body:
                # Keep only the desired visible content: Speaker xx: content.
                speaker_match = re.search(r'(Speaker\s*\d+\s*:\s*.*)$', body, re.IGNORECASE)
                if speaker_match:
                    body = speaker_match.group(1).strip()
                segments.append({
                    'start': start,
                    'end': end,
                    'text': body,
                    'line_number': line_number,
                })

        if segments:
            return sorted(segments, key=lambda item: item['start'])

        # JSON fallback: list or dict containing segments with start/end/text.
        try:
            data = json.loads(content)
            candidates = data.get('segments', []) if isinstance(data, dict) else data
            if isinstance(candidates, list):
                for item in candidates:
                    if not isinstance(item, dict):
                        continue
                    start_value = item.get('start', item.get('start_time'))
                    end_value = item.get('end', item.get('end_time'))
                    body = item.get('text', item.get('utterance', item.get('content', '')))
                    speaker = item.get('speaker', item.get('speaker_id'))
                    if start_value is None or end_value is None or not str(body).strip():
                        continue
                    start = float(start_value) if isinstance(start_value, (int, float)) else cls._timestamp_to_seconds(start_value)
                    end = float(end_value) if isinstance(end_value, (int, float)) else cls._timestamp_to_seconds(end_value)
                    if start is None or end is None or end <= start:
                        continue
                    body = str(body).strip()
                    if not re.match(r'^Speaker\s*\d+\s*:', body, re.IGNORECASE):
                        if speaker is not None:
                            speaker_text = str(speaker).strip()
                            if speaker_text.isdigit():
                                speaker_text = f'Speaker {speaker_text}'
                            elif not speaker_text.lower().startswith('speaker'):
                                speaker_text = f'Speaker {speaker_text}'
                            body = f'{speaker_text}: {body}'
                    segments.append({'start': start, 'end': end, 'text': body})
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        if segments:
            return sorted(segments, key=lambda item: item['start'])

        # SRT/VTT fallback.
        time_range = re.compile(
            r'(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})\s*--?>\s*'
            r'(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})'
        )
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            match = time_range.search(lines[i])
            if not match:
                i += 1
                continue
            start = cls._timestamp_to_seconds(match.group('start'))
            end = cls._timestamp_to_seconds(match.group('end'))
            body_lines = []
            j = i + 1
            while j < len(lines) and lines[j].strip():
                if time_range.search(lines[j]):
                    break
                if not lines[j].strip().isdigit() and lines[j].strip().upper() != 'WEBVTT':
                    body_lines.append(lines[j].strip())
                j += 1
            body = ' '.join(body_lines).strip()
            if start is not None and end is not None and end > start and body:
                segments.append({'start': start, 'end': end, 'text': body})
            i = max(i + 1, j)
        return sorted(segments, key=lambda item: item['start'])

    def _sync_transcript_to_time(self, position):
        """Update the full transcript highlight and the single current caption."""
        if not self.transcript_segments:
            self.current_caption.set('')
            self.active_transcript_segment = None
            return

        active_index = None
        for index, segment in enumerate(self.transcript_segments):
            if segment['start'] <= position < segment['end']:
                active_index = index
                break

        # At gaps between utterances, show no caption and do not highlight a stale line.
        if active_index is None:
            self.current_caption.set('')
            self.transcript_box.tag_remove('active_segment', '1.0', tk.END)
            self.active_transcript_segment = None
            return

        segment = self.transcript_segments[active_index]
        self.current_caption.set(segment['text'])

        if active_index == self.active_transcript_segment:
            return
        self.active_transcript_segment = active_index

        self.transcript_box.tag_remove('active_segment', '1.0', tk.END)
        # Use the parsed source line when available so repeated phrases highlight
        # the correct segment instead of the first identical phrase in the file.
        line_number = segment.get('line_number')
        if line_number is not None:
            start_index = f'{line_number}.0'
            end_index = f'{line_number}.end'
        else:
            start_index = self.transcript_box.search(segment['text'], '1.0', stopindex=tk.END)
            end_index = f'{start_index}+{len(segment["text"])}c' if start_index else None
        if start_index and end_index:
            self.transcript_box.tag_add('active_segment', start_index, end_index)
            self.transcript_box.see(start_index)

    def _load_matching_json_if_needed(self, txt_path):
        """If TXT has no timestamp segments, try a JSON beside it with same stem."""
        if self.transcript_segments:
            return
        json_path = Path(txt_path).with_suffix('.json')
        if not json_path.is_file():
            return
        try:
            json_content = json_path.read_text(encoding='utf-8-sig')
            self.transcript_segments = self._extract_transcript_segments(json_content)
        except (OSError, UnicodeError):
            pass

    def show_transcript_file(self, txt_path):
        path = Path(txt_path)

        try:
            content = path.read_text(encoding="utf-8-sig")

            self.transcript_box.delete("1.0", tk.END)
            self.transcript_box.insert("1.0", content)
            self.transcript_box.tag_remove("active_segment", "1.0", tk.END)
            self.transcript_box.see("1.0")
            self.transcript_segments = self._extract_transcript_segments(content)
            self._load_matching_json_if_needed(path)
            self.active_transcript_segment = None
            self.current_caption.set("")
            self._sync_transcript_to_time(self.player_position)

        except Exception as e:
            messagebox.showerror(
                "Không đọc được TXT",
                f"Không thể đọc file:\n{path}\n\n{e}",
            )

    def choose_transcript_to_read(self):
        path = filedialog.askopenfilename(
            title="Chọn file TXT để đọc",
            filetypes=[
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        self.txt_path.set(path)
        self._show_source_text(path)
        self.show_transcript_file(path)

    def clear_transcript(self):
        self.transcript_box.delete("1.0", tk.END)
        self.transcript_segments = []
        self.active_transcript_segment = None
        self.current_caption.set("")

    # =========================================================
    # STT
    # =========================================================

    def choose_audio(self):
        path = filedialog.askopenfilename(
            title="Chọn file audio",
            filetypes=[
                ("Audio files", "*.mp3 *.wav"),
                ("MP3 files", "*.mp3"),
                ("WAV files", "*.wav"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        if self.stt_thread and self.stt_thread.is_alive():
            messagebox.showinfo(
                "Đang xử lý",
                "STT đang chạy. Vui lòng chờ tác vụ hiện tại.",
            )
            return

        self.audio_path.set(path)

        self.stop_audio()
        self.player_status.set("Audio đã sẵn sàng")

        self.stt_status.set(
            "Đang tải audio lên STT API, vui lòng chờ..."
        )

        self.stt_thread = threading.Thread(
            target=self._stt_worker,
            args=(Path(path),),
            daemon=True,
        )
        self.stt_thread.start()

    def _stt_worker(self, audio_path):
        try:
            output_json, output_txt = transcribe_audio(audio_path)

            self.stt_queue.put(
                (
                    "success",
                    f"Hoàn tất!\nJSON: {output_json}\nTXT: {output_txt}",
                    str(output_txt),
                )
            )

        except requests.exceptions.RequestException as e:
            self.stt_queue.put(
                ("error", format_request_error(e), None)
            )

        except Exception as e:
            self.stt_queue.put(("error", str(e), None))

    # =========================================================
    # SUMMARY
    # =========================================================

    def choose_txt(self):
        path = filedialog.askopenfilename(
            title="Chọn TXT để tóm tắt",
            filetypes=[
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        self.txt_path.set(path)
        self._show_source_text(path)

        self._submit_text_task("summary", Path(path))

    def _show_source_text(self, path):
        try:
            content = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as e:
            messagebox.showerror("Không đọc được TXT", f"Không thể đọc file:\n{path}\n\n{e}")
            return

        self.source_text_box.configure(state="normal")
        self.source_text_box.delete("1.0", tk.END)
        self.source_text_box.insert("1.0", content)
        self.source_text_box.configure(state="disabled")

    def clear_summary(self):
        self.summary_box.delete("1.0", tk.END)

    # =========================================================
    # ASK
    # =========================================================

    def submit_question(self):
        question = self.ask_input.get().strip()

        if not question:
            messagebox.showwarning(
                "Thiếu câu hỏi",
                "Vui lòng nhập câu hỏi.",
            )
            return

        current_txt = self.txt_path.get()

        if current_txt == "Chưa chọn file TXT":
            messagebox.showwarning(
                "Lỗi",
                "Vui lòng chọn file TXT ở phần Summary trước khi hỏi.",
            )
            return

        filename = Path(current_txt).name
        suffix = "_diarization_whisper.txt"
        if filename.endswith(suffix):
            meeting_id = filename[:-len(suffix)]
        else:
            meeting_id = Path(current_txt).stem

        self._submit_text_task(
            "ask",
            {
                "meeting_id": meeting_id,
                "question": question,
            },
        )

    def start_question_recording(self):
        if self.recording_active:
            return

        try:
            import sounddevice as sd
        except ImportError:
            messagebox.showerror(
                "Thiếu thư viện ghi âm",
                "Cài sounddevice để ghi âm câu hỏi:\n\npip install sounddevice",
            )
            return

        self.recording_chunks = []
        try:
            self.recording_stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="int16",
                callback=self._recording_callback,
            )
            self.recording_stream.start()
            self.recording_active = True
            self.record_button.configure(state="disabled")
            self.stop_record_button.configure(state="normal")
            self.ask_status.set("Đang ghi âm câu hỏi...")
        except Exception as e:
            if self.recording_stream:
                try:
                    self.recording_stream.close()
                except Exception:
                    pass
            self.recording_stream = None
            self.record_button.configure(state="normal")
            self.stop_record_button.configure(state="disabled")
            messagebox.showerror("Không thể ghi âm", str(e))

    def _recording_callback(self, indata, _frames, _time_info, status):
        if status:
            print(f"Recording status: {status}")
        with self.recording_lock:
            self.recording_chunks.append(indata.tobytes())

    def stop_question_recording(self):
        if not self.recording_active:
            return

        stream = self.recording_stream
        self.recording_active = False
        self.recording_stream = None
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            self.ask_status.set(f"Lỗi dừng ghi âm: {e}")
            self.record_button.configure(state="normal")
            self.stop_record_button.configure(state="disabled")
            return

        with self.recording_lock:
            audio_data = b"".join(self.recording_chunks)
            self.recording_chunks = []

        self.record_button.configure(state="normal")
        self.stop_record_button.configure(state="disabled")
        if not audio_data:
            self.ask_status.set("Không thu được âm thanh.")
            return

        try:
            fd, audio_path = tempfile.mkstemp(prefix="ask_recording_", suffix=".wav")
            os.close(fd)
            with wave.open(audio_path, "wb") as audio_file:
                audio_file.setnchannels(1)
                audio_file.setsampwidth(2)
                audio_file.setframerate(16000)
                audio_file.writeframes(audio_data)
        except Exception as e:
            self.ask_status.set(f"Không lưu được bản ghi âm: {e}")
            return

        self.ask_status.set("Đang gửi audio lên STT để nhận dạng...")
        self.text_queue.put(("ask_audio", Path(audio_path)))

    # =========================================================
    # TEXT TASK QUEUE
    # =========================================================

    def _submit_text_task(self, task_type, payload):
        if self.stop_event.is_set():
            return

        if task_type == "summary":
            self.summary_status.set("Đang chờ xử lý Summary...")
            self.summary_box.delete("1.0", tk.END)
            self.summary_box.insert("1.0", "Đang tạo bản tóm tắt, vui lòng chờ...")
        else:
            self.ask_status.set("Đang chờ xử lý Ask...")

        self.text_queue.put((task_type, payload))

    def _text_worker(self):
        while not self.stop_event.is_set():
            try:
                task_type, payload = self.text_queue.get(timeout=0.2)

            except queue.Empty:
                continue

            try:
                if self.stop_event.is_set():
                    break

                if task_type == "summary":
                    output_path, answer = summarize_file(payload)

                    self.text_result_queue.put(
                        (
                            "summary",
                            "success",
                            f"Hoàn tất. Đã lưu: {output_path}",
                            answer,
                        )
                    )

                elif task_type == "ask":
                    answer = ask_question(
                        payload["meeting_id"],
                        payload["question"],
                    )

                    self.text_result_queue.put(
                        (
                            "ask",
                            "success",
                            "Đã nhận câu trả lời.",
                            answer,
                        )
                    )

                elif task_type == "ask_audio":
                    audio_path = Path(payload)
                    try:
                        output_json, _output_txt = transcribe_audio(audio_path)
                        result = json.loads(output_json.read_text(encoding="utf-8"))
                        segments = result.get("segments", []) if isinstance(result, dict) else []
                        question = " ".join(
                            str(segment.get("text", "")).strip()
                            for segment in segments
                            if isinstance(segment, dict) and str(segment.get("text", "")).strip()
                        )
                        if not question:
                            raise ValueError("STT không nhận dạng được nội dung câu hỏi.")

                        self.text_result_queue.put(
                            (
                                "ask_audio",
                                "success",
                                "Đã nhận dạng câu hỏi. Bạn có thể chỉnh sửa rồi gửi.",
                                question,
                            )
                        )
                    finally:
                        try:
                            audio_path.unlink(missing_ok=True)
                        except OSError:
                            pass

            except requests.exceptions.RequestException as e:
                self.text_result_queue.put(
                    (
                        task_type,
                        "error",
                        format_request_error(e),
                        "",
                    )
                )

            except Exception as e:
                self.text_result_queue.put(
                    (task_type, "error", str(e), "")
                )

            finally:
                self.text_queue.task_done()

    # =========================================================
    # RESULT HANDLING
    # =========================================================

    def _poll_queues(self):
        if self.stop_event.is_set():
            return

        try:
            while True:
                result = self.stt_queue.get_nowait()
                status, message, output_txt = result

                self.stt_status.set(message)

                if status == "success" and output_txt:
                    self.txt_path.set(output_txt)
                    self._show_source_text(output_txt)
                    self.show_transcript_file(output_txt)

                elif status == "error":
                    messagebox.showerror("STT Error", message)

        except queue.Empty:
            pass

        try:
            while True:
                result = self.text_result_queue.get_nowait()

                task_type, status, message, answer = result

                if task_type == "summary":
                    self.summary_status.set(message)
                else:
                    self.ask_status.set(message)

                if status == "success":
                    if task_type == "summary":
                        self.summary_box.delete("1.0", tk.END)
                        self.summary_box.insert("1.0", answer or "Không có nội dung Summary được trả về.")
                        self.summary_box.see("1.0")
                    elif task_type == "ask_audio":
                        self.ask_input.delete(0, tk.END)
                        self.ask_input.insert(0, answer)
                    else:
                        self.result_box.delete("1.0", tk.END)
                        self.result_box.insert(tk.END, answer)

                elif task_type == "summary":
                    self.summary_box.delete("1.0", tk.END)
                    self.summary_box.insert("1.0", f"Không thể tạo Summary:\n{message}")
                else:
                    messagebox.showerror("API Error", message)

        except queue.Empty:
            pass

        self.root.after(100, self._poll_queues)

    # =========================================================
    # CLOSE
    # =========================================================

    def _on_close(self):
        self.stop_event.set()

        if self.recording_active and self.recording_stream:
            try:
                self.recording_stream.stop()
                self.recording_stream.close()
            except Exception:
                pass
            self.recording_active = False

        if self.audio_player_ready:
            try:
                self.pygame.mixer.music.stop()
                self.pygame.mixer.quit()
            except Exception:
                pass

        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = AIMeetingSummaryApp(root)
    root.mainloop()
