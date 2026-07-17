"""Read newly-created Windows WeChat network statistics to download a live replay.

This module never controls or reads the WeChat UI. The user opens the target share
link and starts playback while this process watches narrowly-scoped local net logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from isaac64 import ENCRYPTED_PREFIX, decrypt_prefix


URL_RE = re.compile(rb"https?://[A-Za-z0-9:/?&=+%_.~,@!$*;#\-\[\]]{12,}")
DECIMAL_RE = re.compile(rb"(?<![A-Za-z0-9])[0-9]{6,20}(?![A-Za-z0-9])")
FEED_ID_RE = re.compile(rb'feedId["\\]*\s*[:=]["\\]*([0-9]{15,20})')
MAX_LOG_BYTES = 8 * 1024 * 1024
WECHAT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger WindowsWechat"
)
STATE_DIR = Path.home() / ".wechat-video-subtitle" / "replay-state"


class ReplayActionRequired(RuntimeError):
    """Signal that the harness must return control to the user before capture."""

    def __init__(self, share_link: str):
        super().__init__("直播回放捕获已准备，需要用户重新打开并播放目标链接。")
        self.share_link = share_link


@dataclass(frozen=True)
class ReplayCandidate:
    url: str
    keys: tuple[int, ...]
    feed_id: str = ""
    expected_size: int = 0
    active: bool = False

    @property
    def complete(self) -> bool:
        query = parse_qs(urlsplit(self.url).query)
        return bool(query.get("encfilekey") and query.get("token"))


def is_media_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host == "finder.video.qq.com" and parsed.path.startswith(
        ("/251/20302/", "/251/20304/")
    )


def media_identity(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    return (query.get("encfilekey") or [url])[0]


def read_log(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > MAX_LOG_BYTES:
                handle.seek(size - MAX_LOG_BYTES)
            return handle.read(MAX_LOG_BYTES)
    except (OSError, PermissionError):
        return b""


def extract_candidates(path: Path) -> list[ReplayCandidate]:
    data = read_log(path)
    if not data:
        return []

    active_feeds = {value.decode("ascii") for value in FEED_ID_RE.findall(data)}
    keys = {int(value) for value in DECIMAL_RE.findall(data)}
    keys.update(
        int(value)
        for value in re.findall(rb"\d+", path.name.encode("ascii", errors="ignore"))
        if 6 <= len(value) <= 20
    )
    sorted_keys = tuple(sorted(keys))

    raw: list[tuple[str, str, int]] = []
    for match in URL_RE.finditer(data):
        try:
            url = match.group(0).decode("ascii")
        except UnicodeDecodeError:
            continue
        if not is_media_url(url):
            continue
        before = data[max(0, match.start() - 64) : match.start()]
        feed_matches = re.findall(rb"([0-9]{15,20}),?$", before)
        feed_id = feed_matches[-1].decode("ascii") if feed_matches else ""
        after = data[match.end() : match.end() + 2048]
        totals = re.findall(rb'content-range[^/]{0,80}/([0-9]{6,15})', after)
        raw.append((url, feed_id, int(totals[0]) if totals else 0))

    metadata: dict[str, tuple[str, int, bool]] = {}
    for url, feed_id, expected_size in raw:
        identity = media_identity(url)
        current = metadata.get(identity, ("", 0, False))
        metadata[identity] = (
            feed_id or current[0],
            max(expected_size, current[1]),
            current[2] or bool(feed_id and feed_id in active_feeds),
        )

    candidates: dict[str, ReplayCandidate] = {}
    for url, feed_id, expected_size in raw:
        group_feed, group_size, group_active = metadata[media_identity(url)]
        candidates[url] = ReplayCandidate(
            url=url,
            keys=sorted_keys,
            feed_id=feed_id or group_feed,
            expected_size=max(expected_size, group_size),
            active=group_active,
        )
    return list(candidates.values())


def net_directories() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    roots = [appdata / "Tencent/xwechat", appdata / "Tencent/WeChat"]
    result: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            result.extend(
                child
                for child in root.iterdir()
                if child.is_dir() and (child.name == "net" or child.name.startswith("net_"))
            )
        except OSError:
            continue
    return result


def recent_log_files(directories: list[Path], since: float) -> list[Path]:
    result: list[Path] = []
    for directory in directories:
        for dirpath, _, filenames in os.walk(directory):
            for filename in filenames:
                path = Path(dirpath, filename)
                try:
                    if path.stat().st_mtime >= since and path.stat().st_size > 0:
                        result.append(path)
                except OSError:
                    continue
    return result


def session_prefix(path: Path) -> str:
    name = path.name.removeprefix("key_")
    parts = name.split("_")
    return "_".join(parts[:3]) + "_" if len(parts) >= 3 else ""


def choose_candidate(candidates: list[ReplayCandidate]) -> ReplayCandidate | None:
    usable = [candidate for candidate in candidates if candidate.complete]
    if not usable:
        return None
    usable.sort(
        key=lambda item: (item.active, item.expected_size, len(item.url)), reverse=True
    )
    return usable[0] if usable[0].active else None


def state_path(share_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", share_id)
    return STATE_DIR / f"{safe_id}.json"


def arm_replay(share_link: str) -> dict:
    share_id = urlsplit(share_link).path.rstrip("/").split("/")[-1]
    if not share_id:
        raise ValueError("无效的视频号分享链接。")
    if not net_directories():
        raise RuntimeError("未找到 Windows 微信网络统计目录；请确认已安装并登录电脑版微信。")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {"version": 1, "share_id": share_id, "armed_at": time.time()}
    path = state_path(share_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)
    print(f"REPLAY_ARMED share_id={share_id}", flush=True)
    print(
        "ACTION_REQUIRED 请现在在电脑版微信中重新打开目标链接并开始播放几秒，"
        "然后回复 Agent“已播放”。",
        flush=True,
    )
    return state


def load_arm(share_id: str, max_age_seconds: int = 30 * 60) -> dict:
    path = state_path(share_id)
    if not path.is_file():
        raise RuntimeError("没有找到该回放的准备状态；请先运行 replay-arm。")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        armed_at = float(state["armed_at"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("直播回放准备状态损坏，请重新运行 replay-arm。") from error
    if state.get("share_id") != share_id or time.time() - armed_at > max_age_seconds:
        raise RuntimeError("直播回放准备状态已过期，请重新运行 replay-arm。")
    return state


def capture_since(share_id: str, started: float, timeout: int) -> ReplayCandidate:
    directories = net_directories()
    if not directories:
        raise RuntimeError("未找到 Windows 微信网络统计目录；请确认已安装并登录电脑版微信。")

    marker = f"weixin.qq.com/sph/{share_id}".encode("ascii")
    print("CAPTURE_MODE local-log-read-only no-ui-control no-proxy no-certificate", flush=True)

    target_prefix = ""
    first_media_at = 0.0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = recent_log_files(directories, started - 2)
        if not target_prefix:
            for path in sorted(files, key=lambda item: item.stat().st_mtime, reverse=True):
                if marker in read_log(path):
                    target_prefix = session_prefix(path)
                    print("SHARE_SESSION_MATCHED", flush=True)
                    break

        if target_prefix:
            session_files = [path for path in files if path.name.removeprefix("key_").startswith(target_prefix)]
            candidates: list[ReplayCandidate] = []
            for path in session_files:
                candidates.extend(extract_candidates(path))
            selected = choose_candidate(candidates)
            if selected:
                if not first_media_at:
                    first_media_at = time.monotonic()
                if time.monotonic() - first_media_at >= 3:
                    print(
                        f"TARGET_STREAM_MATCHED active_feed=1 "
                        f"size_mb={selected.expected_size / 1024 / 1024:.1f}",
                        flush=True,
                    )
                    return selected
        time.sleep(0.5)
    raise TimeoutError("等待直播回放播放流超时；请重新运行命令后再打开目标链接。")


def capture_target(share_id: str, timeout: int = 240) -> ReplayCandidate:
    print("ACTION_REQUIRED 请在电脑版微信中打开目标链接并开始播放；无需等待播放完成。", flush=True)
    return capture_since(share_id, time.time(), timeout)


def capture_armed(share_id: str, timeout: int = 45) -> ReplayCandidate:
    state = load_arm(share_id)
    print(f"REPLAY_STATE_LOADED share_id={share_id}", flush=True)
    return capture_since(share_id, float(state["armed_at"]), timeout)


def request_headers(start: int | None = None, end: int | None = None) -> dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": "https://channels.weixin.qq.com/",
        "User-Agent": WECHAT_UA,
    }
    if start is not None:
        headers["Range"] = f"bytes={start}-{'' if end is None else end}"
    return headers


def find_decode_key(candidate: ReplayCandidate, encrypted: bytes) -> int:
    for key in candidate.keys:
        clear = decrypt_prefix(encrypted, key)
        if clear[4:8] in {b"ftyp", b"styp", b"moov", b"mdat"} or b"ftyp" in clear[:32]:
            print(f"DECODE_KEY_MATCHED candidates={len(candidate.keys)}", flush=True)
            return key
    raise RuntimeError("未能从目标播放会话中匹配直播回放解密参数。")


def open_stream(candidate: ReplayCandidate):
    response = requests.get(
        candidate.url,
        headers=request_headers(),
        stream=True,
        timeout=(20, 60),
    )
    response.raise_for_status()
    return response


def stream_decrypted(candidate: ReplayCandidate, sink) -> None:
    with open_stream(candidate) as response:
        prefix = response.raw.read(ENCRYPTED_PREFIX)
        if not prefix:
            raise RuntimeError("直播回放媒体响应为空。")
        key = find_decode_key(candidate, prefix)
        sink.write(decrypt_prefix(prefix, key))
        downloaded = len(prefix)
        last_report = time.monotonic()
        while True:
            chunk = response.raw.read(1024 * 1024)
            if not chunk:
                break
            sink.write(chunk)
            downloaded += len(chunk)
            now = time.monotonic()
            if now - last_report >= 10:
                if candidate.expected_size:
                    percent = min(100.0, downloaded / candidate.expected_size * 100)
                    print(
                        f"DOWNLOAD_PROGRESS {percent:.1f}% "
                        f"({downloaded / 1024 / 1024:.1f} MiB)",
                        flush=True,
                    )
                else:
                    print(
                        f"DOWNLOAD_PROGRESS {downloaded / 1024 / 1024:.1f} MiB",
                        flush=True,
                    )
                last_report = now
        print(f"MEDIA_STREAM_COMPLETE bytes={downloaded}", flush=True)


def save_video(candidate: ReplayCandidate, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".part")
    print("开始下载并流式解密直播回放视频...", flush=True)
    try:
        with partial.open("wb") as handle:
            stream_decrypted(candidate, handle)
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return output


class FfmpegSink:
    def __init__(self, process: subprocess.Popen):
        self.process = process

    def write(self, data: bytes) -> None:
        if self.process.stdin is None:
            raise RuntimeError("FFmpeg stdin 不可用。")
        self.process.stdin.write(data)


def save_audio(candidate: ReplayCandidate, output: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg。")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0",
        "-vn", "-c:a", "libmp3lame", "-b:a", "96k", str(output),
    ]
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        stream_decrypted(candidate, FfmpegSink(process))
    except (BrokenPipeError, OSError):
        pass
    except BaseException:
        process.kill()
        process.wait()
        output.unlink(missing_ok=True)
        raise
    finally:
        if process.stdin:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    code = process.wait(timeout=120)
    if code != 0 or not output.is_file() or output.stat().st_size < 64 * 1024:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg 提取直播回放音频失败: {stderr[-600:]}")
    return output


def capture_and_download(
    share_link: str,
    save_dir: Path,
    audio_only: bool = False,
    timeout: int = 240,
    armed: bool = False,
) -> dict:
    share_id = urlsplit(share_link).path.rstrip("/").split("/")[-1]
    if not share_id:
        raise ValueError("无效的视频号分享链接。")
    candidate = (
        capture_armed(share_id, timeout=min(timeout, 60))
        if armed
        else capture_target(share_id, timeout=timeout)
    )
    fingerprint = hashlib.sha256(candidate.url.encode("ascii")).hexdigest()[:10]
    title = f"live_replay_{share_id}"
    suffix = ".mp3" if audio_only else ".mp4"
    output = save_dir / f"{title}_{fingerprint}{suffix}"
    path = save_audio(candidate, output) if audio_only else save_video(candidate, output)
    if armed:
        state_path(share_id).unlink(missing_ok=True)
    return {"path": path, "author": "", "title": title, "share_id": share_id}
