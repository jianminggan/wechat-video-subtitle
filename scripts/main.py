"""CLI entry point for WeChat Channels download and transcription."""

import hashlib
import os
import site
import shutil
import sys
from pathlib import Path


def _load_optional_env() -> None:
    candidates = [
        Path(__file__).resolve().parents[1] / ".env",
        Path.home() / ".wechat-video-subtitle" / ".env",
        Path.home() / ".hermes" / ".env",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


def _ensure_user_site_packages() -> None:
    """Hermes may run main.py directly with user site disabled."""
    try:
        user_site = site.getusersitepackages()
    except (AttributeError, RuntimeError):
        return
    if user_site and user_site not in sys.path and Path(user_site).exists():
        sys.path.insert(0, user_site)


_load_optional_env()
_ensure_user_site_packages()

from download import (
    download_single_video,
    ensure_output_dir,
    get_video_files,
    normalize_download_result,
    safe_filename,
)
from replay_capture import ReplayActionRequired, arm_replay, capture_and_download
from transcribe import process_multiple_videos, process_video


OUTPUT_DIR = Path(
    os.environ.get("WECHAT_OUTPUT_DIR", str(Path.home() / "Downloads" / "WeChat-video-download"))
)


def print_banner():
    print("=" * 60)
    print("  微信视频号字幕提取工具")
    print("=" * 60)
    print(f"  输出目录: {OUTPUT_DIR}")
    print("  输出由命令决定: transcript=逐字稿，download=视频")
    print("=" * 60)


def task_paths(title: str) -> tuple[Path, Path, Path, Path]:
    task_name = safe_filename(title, "wechat-video")
    if len(task_name) > 72:
        digest = hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:8]
        task_name = f"{task_name[:63]}_{digest}"
    root = OUTPUT_DIR / task_name
    return root, root / "video", root / "audio", root / "transcript"


def place_downloaded_video(result: dict) -> tuple[Path, Path]:
    source = Path(result["path"])
    root, video_dir, _, _ = task_paths(result.get("title") or source.stem)
    video_dir.mkdir(parents=True, exist_ok=True)
    max_stem = max(40, 235 - len(str(video_dir)) - len(source.suffix) - 1)
    destination_stem = source.stem
    if len(destination_stem) > max_stem:
        tail = destination_stem[-32:]
        destination_stem = f"{destination_stem[:max_stem - len(tail) - 1]}_{tail}"
    destination = video_dir / f"{destination_stem}{source.suffix}"
    if source.resolve() != destination.resolve():
        if destination.exists() and destination.stat().st_size == source.stat().st_size:
            source.unlink()
        else:
            if destination.exists():
                destination = video_dir / f"{destination.stem}_{int(source.stat().st_mtime)}{destination.suffix}"
            shutil.move(str(source), str(destination))
    return root, destination


def remove_downloaded_video(video_path: Path):
    video_dir = video_path.parent
    video_path.unlink(missing_ok=True)
    try:
        video_dir.rmdir()
    except OSError:
        pass
    print("已清理下载视频")


def process_download_result(result: dict, keep_video: bool, keep_audio: bool) -> bool:
    root, video_path = place_downloaded_video(result)
    author = result.get("author", "")
    title = result.get("title") or video_path.stem
    print(f"\n开始转写: {title}")
    transcript = process_video(
        video_path,
        author=author,
        title=title,
        job_dir=root,
        keep_audio=keep_audio,
    )
    if not transcript:
        print(f"转写失败，中间文件已保留在: {root}")
        return False
    if not keep_video:
        remove_downloaded_video(video_path)
    print(f"最终逐字稿: {transcript}")
    return True


def cmd_single(link: str, keep_video: bool = False, keep_audio: bool = False) -> bool:
    print("\n处理单条视频...")
    result = download_single_video(link, replay_audio_only=not keep_video)
    if not result:
        print("下载失败，无法转写")
        return False
    return process_download_result(result, keep_video=keep_video, keep_audio=keep_audio)


def cmd_download(link: str) -> bool:
    print("\n只下载视频...")
    result = download_single_video(link)
    if not result:
        print("下载失败")
        return False
    root, video_path = place_downloaded_video(result)
    print(f"视频已保存: {video_path}")
    print(f"任务目录: {root}")
    return True


def cmd_batch(links_file: str, keep_video: bool = False, keep_audio: bool = False) -> bool:
    file_path = Path(links_file)
    if not file_path.exists():
        print(f"文件不存在: {links_file}")
        return False
    links = [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"共 {len(links)} 个视频链接")
    success = 0
    for index, link in enumerate(links, 1):
        print(f"\n[{index}/{len(links)}] 处理: {link[:80]}")
        result = download_single_video(link, replay_audio_only=not keep_video)
        if result:
            if process_download_result(result, keep_video=keep_video, keep_audio=keep_audio):
                success += 1
    print(f"批量处理完成: {success}/{len(links)}")
    return bool(links) and success == len(links)


def cmd_transcribe(path_str: str, keep_audio: bool = False) -> bool:
    path = Path(path_str)
    if path.is_file():
        root, _, _, _ = task_paths(path.stem)
        print(f"\n转写本地文件: {path}")
        return bool(process_video(path, title=path.stem, job_dir=root, keep_audio=keep_audio))
    if path.is_dir():
        files = get_video_files(path)
        if not files:
            extensions = {".mp4", ".mkv", ".webm", ".mov"}
            files = sorted(item for item in path.rglob("*") if item.suffix.lower() in extensions)
        if files:
            print(f"找到 {len(files)} 个视频文件")
            return process_multiple_videos(files) == len(files)
        else:
            print(f"目录中没有找到视频文件: {path}")
        return False
    print(f"路径不存在: {path}")
    return False


def cmd_auth(timeout: int = 300) -> bool:
    from yuanbao_direct import authenticate

    authenticate(timeout)
    return True


def cmd_replay_arm(link: str) -> bool:
    arm_replay(link)
    return True


def cmd_replay_download(link: str) -> bool:
    result = capture_and_download(link, ensure_output_dir("videos"), armed=True)
    result = normalize_download_result(result, "两阶段直播回放下载")
    if not result:
        return False
    root, video_path = place_downloaded_video(result)
    print(f"视频已保存: {video_path}")
    print(f"任务目录: {root}")
    return True


def cmd_replay_transcript(
    link: str, keep_video: bool = False, keep_audio: bool = False
) -> bool:
    result = capture_and_download(
        link,
        ensure_output_dir("videos"),
        audio_only=not keep_video,
        armed=True,
    )
    if keep_video:
        result = normalize_download_result(result, "两阶段直播回放下载")
        if not result:
            return False
    return process_download_result(result, keep_video=keep_video, keep_audio=keep_audio)


def has_flag(args: list[str], flag: str) -> bool:
    return flag in args


def print_usage():
    print_banner()
    print("\n用法:")
    print("  python main.py transcript <分享链接> [--keep-video] [--keep-audio]")
    print("  python main.py single <分享链接> [--keep-video] [--keep-audio]  # 兼容旧命令")
    print("  python main.py download <分享链接>")
    print("  python main.py batch <链接列表> [--keep-video] [--keep-audio]")
    print("  python main.py transcribe <本地视频或目录> [--keep-audio]")
    print("  python main.py replay-arm <直播回放链接>")
    print("  python main.py replay-download <直播回放链接>")
    print("  python main.py replay-transcript <直播回放链接> [--keep-video] [--keep-audio]")
    print("\ntranscript/single/batch 默认只保留逐字稿；download 只保留视频。")
    print("明确需要视频和逐字稿时，为 transcript 添加 --keep-video。")


def main() -> int:
    if len(sys.argv) < 2:
        print_usage()
        return 2
    command = sys.argv[1]
    args = sys.argv[2:]
    if command in {
        "transcript", "single", "download", "batch", "transcribe",
        "replay-arm", "replay-download", "replay-transcript",
    } and not args:
        print_usage()
        return 2
    try:
        if command in {"transcript", "single"}:
            ok = cmd_single(args[0], has_flag(args, "--keep-video"), has_flag(args, "--keep-audio"))
        elif command == "download":
            ok = cmd_download(args[0])
        elif command == "batch":
            ok = cmd_batch(args[0], has_flag(args, "--keep-video"), has_flag(args, "--keep-audio"))
        elif command == "transcribe":
            ok = cmd_transcribe(args[0], has_flag(args, "--keep-audio"))
        elif command == "replay-arm":
            ok = cmd_replay_arm(args[0])
        elif command == "replay-download":
            ok = cmd_replay_download(args[0])
        elif command == "replay-transcript":
            ok = cmd_replay_transcript(
                args[0], has_flag(args, "--keep-video"), has_flag(args, "--keep-audio")
            )
        elif command == "auth":
            ok = cmd_auth()
        else:
            print(f"未知命令: {command}")
            print_usage()
            return 2
    except ReplayActionRequired as error:
        next_command = (
            "replay-download" if command == "download" else "replay-transcript"
        )
        flags = []
        if has_flag(args, "--keep-video"):
            flags.append("--keep-video")
        if has_flag(args, "--keep-audio"):
            flags.append("--keep-audio")
        print(f"NEXT_COMMAND {next_command} {error.share_link} {' '.join(flags)}".rstrip())
        print(
            '{"status":"action_required","action":"open_and_play_in_wechat",'
            f'"next_command":"{next_command}"}}'
        )
        return 3
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
