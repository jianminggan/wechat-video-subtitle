"""
字幕转写模块
使用 Mimo ASR API（chat/completions + input_audio 格式）进行语音转文字
支持 Whisper 作为备选
输出格式: 仅 .md 文件，带标点符号和分段
"""

import os
import re
import json
import base64
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
import requests

def load_optional_env() -> None:
    """Load optional config files without overriding process environment variables."""
    candidates = [
        Path(__file__).resolve().parents[1] / ".env",
        Path.home() / ".wechat-video-subtitle" / ".env",
        Path.home() / ".hermes" / ".env",  # Backward compatibility.
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)


load_optional_env()

OUTPUT_DIR = Path(
    os.environ.get("WECHAT_OUTPUT_DIR", str(Path.home() / "Downloads" / "stt_output"))
)

# ASR API 配置（环境变量优先，也可读取项目或用户配置文件）
ASR_API_KEY = os.environ.get("ASR_API_KEY", "")
ASR_BASE_URL = os.environ.get("ASR_BASE_URL", os.environ.get("ASR_API_BASE_URL", ""))
ASR_MODEL = os.environ.get("ASR_MODEL", "mimo-v2.5-asr")

# Whisper 备选
USE_WHISPER = os.environ.get("USE_WHISPER", "").lower() in ("true", "1", "yes")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large")

# LLM 后处理配置（可选）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")


def extract_audio(video_path: Path) -> Optional[Path]:
    """用 ffmpeg 提取音频（16kHz, mono, wav）"""
    audio_dir = OUTPUT_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = audio_dir / f"{video_path.stem}.wav"

    print(f"提取音频: {video_path.name}")
    try:
        cmd = ["ffmpeg", "-i", str(video_path), "-vn", "-ar", "16000", "-ac", "1", "-y", str(output_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            size_mb = output_path.stat().st_size / 1024 / 1024
            print(f"音频提取完成: {output_path.name} ({size_mb:.1f}MB)")
            return output_path
        else:
            print(f"ffmpeg 失败: {result.stderr[:200]}")
            return None
    except subprocess.TimeoutExpired:
        print("ffmpeg 超时")
        return None
    except FileNotFoundError:
        print("ffmpeg 未安装")
        return None


def extract_audio_segments(
    video_path: Path,
    audio_dir: Path,
    segment_seconds: int = 300,
) -> list[Path]:
    """Extract compact MP3 chunks so long replays do not exceed ASR request limits."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(audio_dir.glob("chunk_*.mp3"))
    if existing and all(path.stat().st_size > 0 for path in existing):
        print(f"复用已提取的 {len(existing)} 个音频分片")
        return existing

    output_pattern = audio_dir / "chunk_%03d.mp3"
    print(f"提取 {segment_seconds // 60} 分钟音频分片: {video_path.name}")
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video_path), "-vn",
        "-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1", "-y", str(output_pattern),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        print("音频分片提取超时")
        return []
    except FileNotFoundError:
        print("ffmpeg 未安装")
        return []
    if result.returncode != 0:
        print(f"音频分片提取失败: {result.stderr[-500:]}")
        return []
    chunks = sorted(audio_dir.glob("chunk_*.mp3"))
    print(f"音频分片完成: {len(chunks)} 个")
    return chunks


def transcribe_with_mimo(audio_path: Path) -> Optional[str]:
    """使用 Mimo ASR API（chat/completions + input_audio 格式）"""
    if not ASR_API_KEY or not ASR_BASE_URL:
        print("ASR API 未配置，请设置环境变量或 .env 文件")
        return None

    print(f"使用 Mimo ASR API ({ASR_MODEL})...")
    url = f"{ASR_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {ASR_API_KEY}", "Content-Type": "application/json"}

    # 读取音频文件并 base64 编码
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    ext = audio_path.suffix.lower().lstrip(".")
    fmt_map = {"mp3": "mp3", "wav": "wav", "m4a": "m4a", "ogg": "ogg", "flac": "flac"}
    audio_fmt = fmt_map.get(ext, "wav")

    payload = {
        "model": ASR_MODEL,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": audio_b64, "format": audio_fmt}
            }]
        }]
    }

    try:
        print("正在调用 Mimo ASR API...（可能需要几分钟）")
        resp = requests.post(url, headers=headers, json=payload, timeout=600)
        resp.raise_for_status()
        result = resp.json()
        text = result["choices"][0]["message"]["content"]
        print(f"转写完成: {len(text)} 字符")
        return text
    except requests.exceptions.Timeout:
        print("Mimo ASR API 请求超时")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Mimo ASR API 调用失败: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"响应: {e.response.text[:300]}")
        return None
    except (KeyError, IndexError) as e:
        print(f"解析响应失败: {e}")
        return None


def transcribe_with_whisper(audio_path: Path) -> Optional[str]:
    """使用 OpenAI Whisper 作为备选"""
    print(f"使用 Whisper 模型: {WHISPER_MODEL}...")
    try:
        import whisper
    except ImportError:
        print("Whisper 未安装")
        return None

    try:
        model = whisper.load_model(WHISPER_MODEL)
        print("正在识别...")
        start = time.time()
        result = model.transcribe(str(audio_path), language="zh", verbose=False)
        elapsed = time.time() - start
        text = result.get("text", "")
        print(f"转写完成: {len(text)} 字符，耗时 {elapsed:.0f} 秒")
        return text
    except Exception as e:
        print(f"Whisper 识别失败: {e}")
        return None


def post_process_with_llm(text: str) -> str:
    """使用 LLM 进行后处理：纠错 + 标点 + 分段"""
    if not LLM_API_KEY or not LLM_BASE_URL or not LLM_MODEL:
        print("LLM 后处理未配置（设置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL），跳过")
        return text

    print("进行 LLM 纠错和格式化...")
    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}

    prompt = f"""你是一个专业的语音转文字后处理助手。请对以下 ASR 语音识别的原始文本进行处理：

处理要求：
1. 纠错：修正同音字错误、品牌/人名误识别、专业术语、断句错误。不确定的词不要猜测
2. 标点：添加完整的标点符号（句号、逗号、问号、感叹号等）
3. 分段：按语义分成段落，每段 3-5 句话，段落之间用空行分隔
4. 保留原样：保留重复、口头禅、语气词（嗯、啊、就是等），不做总结、润色、改写

只输出处理后的文本，不要加任何说明、标题、总结。

原始文本：
{text}"""

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        processed = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"LLM 后处理完成: {len(processed)} 字符")
        return processed
    except Exception as e:
        print(f"LLM 后处理失败: {e}，使用原始文本")
        return text


def basic_post_process(text: str) -> str:
    """基本后处理：简单分段（无 LLM 时的备选）"""
    # 按句号、问号、感叹号、省略号分割句子
    sentences = re.split(r'([。！？\n])', text)
    chunks = []
    current = ""
    for s in sentences:
        if s.strip():
            current += s
            if s in "。！？" and len(current) > 20:
                chunks.append(current.strip())
                current = ""
    if current.strip():
        chunks.append(current.strip())

    # 每 3-5 句一段
    paragraphs = []
    for i in range(0, len(chunks), 4):
        para = "".join(chunks[i:i+4])
        if para.strip():
            paragraphs.append(para.strip())

    return "\n\n".join(paragraphs) if paragraphs else text


def transcribe_segment(index: int, total: int, audio_path: Path) -> tuple[int, Optional[str]]:
    transcript_cache = audio_path.with_suffix(".txt")
    if transcript_cache.exists() and transcript_cache.stat().st_size > 0:
        text = transcript_cache.read_text(encoding="utf-8")
        print(f"[{index + 1}/{total}] 复用已转写分片: {audio_path.name}")
        return index, text

    print(f"[{index + 1}/{total}] 转写: {audio_path.name}")
    text = None
    for attempt in range(1, 4):
        if not USE_WHISPER:
            text = transcribe_with_mimo(audio_path)
        if not text:
            text = transcribe_with_whisper(audio_path)
        if text and text.strip():
            transcript_cache.write_text(text, encoding="utf-8")
            return index, text
        if attempt < 3:
            print(f"{audio_path.name} 转写失败，5 秒后重试 ({attempt}/3)")
            time.sleep(5)
    return index, None


def save_markdown(text: str, output_path: Path, title: str = ""):
    """保存为 .md 格式"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        if title:
            f.write(f"# {title}\n\n")
        f.write(text)
    print(f"逐字稿已保存: {output_path}")


def process_video(
    video_path: Path,
    author: str = "",
    title: str = "",
    job_dir: Optional[Path] = None,
    keep_audio: bool = False,
) -> Optional[Path]:
    """
    处理单个视频：提取音频 -> 转写 -> 后处理 -> 保存 .md
    """
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)[:100] if title else video_path.stem
    job_dir = job_dir or OUTPUT_DIR / safe_title
    audio_dir = job_dir / "audio"
    transcript_dir = job_dir / "transcript"
    segment_seconds = max(60, int(os.environ.get("ASR_SEGMENT_SECONDS", "300")))

    # 1. 提取音频分片，长视频也能稳定处理。
    audio_paths = extract_audio_segments(video_path, audio_dir, segment_seconds)
    if not audio_paths:
        return None

    # 2. 逐片转写并缓存结果，失败后可从已完成分片继续。
    raw_parts = [None] * len(audio_paths)
    worker_count = 1 if USE_WHISPER else max(1, int(os.environ.get("ASR_WORKERS", "4")))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(transcribe_segment, index, len(audio_paths), audio_path)
            for index, audio_path in enumerate(audio_paths)
        ]
        for future in as_completed(futures):
            index, text = future.result()
            if not text or not text.strip():
                print(f"分片转写失败: {audio_paths[index].name}")
                return None
            raw_parts[index] = text.strip()
    raw_text = "\n\n".join(raw_parts)

    # 3. 后处理：纠错 + 标点 + 分段
    processed_text = post_process_with_llm(raw_text)
    if processed_text == raw_text:
        processed_text = basic_post_process(raw_text)

    # 4. 所有产物放在同一个任务根目录下。
    output_path = transcript_dir / f"{safe_title}.md"

    # 5. 保存
    save_markdown(processed_text, output_path, title=safe_title)
    if not keep_audio and audio_dir.exists():
        shutil.rmtree(audio_dir)
        print("已清理音频分片")
    print(f"\n处理完成: {safe_title}")
    return output_path


def process_multiple_videos(video_files: list, author: str = "", title_prefix: str = "") -> int:
    """批量处理多个视频"""
    success = 0
    total = len(video_files)

    for i, video_path in enumerate(video_files, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{total}] 处理: {video_path.name}")
        print(f"{'='*60}")
        vid_title = f"{title_prefix}_{i:03d}" if title_prefix else video_path.stem
        if process_video(video_path, author=author, title=vid_title):
            success += 1

    print(f"\n全部完成: {success}/{total} 个视频成功")
    return success
