"""
视频号视频下载模块
支持普通视频、可网页预览的视频，以及直播回放页面的探测。
"""

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests

OUTPUT_DIR = Path(
    os.environ.get("WECHAT_OUTPUT_DIR", str(Path.home() / "Downloads" / "stt_output"))
)
MIN_VIDEO_BYTES = int(os.environ.get("WECHAT_MIN_VIDEO_BYTES", str(1024 * 1024)))
WECHAT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def ensure_output_dir(subdir: str = "") -> Path:
    """确保输出目录存在"""
    path = OUTPUT_DIR / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


class UnsupportedReplayError(Exception):
    """Raised when a share link is a client-only live replay without a web media URL."""

    def __init__(self, message: str, dynamic_export_id: str = "", hint: str = ""):
        super().__init__(message)
        self.dynamic_export_id = dynamic_export_id
        self.hint = hint


def safe_filename(name: str, default: str = "video") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip(" ._")
    return (name[:120] or default).strip()


def get_short_uri(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if query.get("id"):
        return query["id"][0]
    parts = [p for p in parsed.path.split("/") if p]
    return parts[-1] if parts else url


def normalize_media_url(url: str, base_url: str = "") -> str:
    url = (url or "").replace("\\u0026", "&").replace("&amp;", "&")
    if url.startswith("//"):
        return "https:" + url
    if base_url and url.startswith("/"):
        return urljoin(base_url, url)
    return url


def validate_video_file(path: Path) -> tuple[bool, str]:
    """Reject cover images and error pages even when they were saved with an .mp4 suffix."""
    if not path.exists() or not path.is_file():
        return False, "文件不存在"
    size = path.stat().st_size
    if size < MIN_VIDEO_BYTES:
        return False, f"文件过小（{size} 字节，小于 {MIN_VIDEO_BYTES} 字节）"

    with path.open("rb") as handle:
        header = handle.read(512)
    if header.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")):
        return False, "文件头是图片，不是视频"
    if header.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<?xml")):
        return False, "文件内容是网页，不是视频"

    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return (b"ftyp" in header[:64], "ffprobe 未安装且文件头不是 MP4 ftyp")
    except subprocess.TimeoutExpired:
        return False, "ffprobe 校验超时"
    if probe.returncode != 0 or "video" not in probe.stdout.split():
        detail = (probe.stderr or probe.stdout).strip()[-300:]
        return False, f"ffprobe 未发现视频流{(': ' + detail) if detail else ''}"
    return True, "ok"


def discard_invalid_video(path: Path, strategy: str) -> bool:
    valid, reason = validate_video_file(path)
    if valid:
        print(f"视频校验通过: {path.name} ({path.stat().st_size / 1024 / 1024:.1f} MiB)")
        return False
    print(f"{strategy} 返回的文件无效: {reason}")
    path.unlink(missing_ok=True)
    return True


def normalize_download_result(result, strategy: str) -> Optional[dict]:
    if not result:
        return None
    normalized = (
        {"path": result, "author": "", "title": result.stem}
        if isinstance(result, Path)
        else result
    )
    path = Path(normalized["path"])
    if discard_invalid_video(path, strategy):
        return None
    normalized["path"] = path
    return normalized


def download_with_requests(url: str, save_path: Path, referer: str = "") -> bool:
    """用 requests 下载 mp4/webm/mov 等直链文件"""
    try:
        print(f"下载中: {save_path.name}")
        headers = {"User-Agent": WECHAT_UA}
        if referer:
            headers["Referer"] = referer
        with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(
                            f"  进度: {pct:.0f}% "
                            f"({downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB)"
                        )
        print(f"下载完成: {save_path}")
        return True
    except Exception as e:
        print(f"下载失败: {e}")
        return False


def download_with_ffmpeg(url: str, save_path: Path, referer: str = "") -> bool:
    """用 ffmpeg 下载 HLS/m3u8 或需要合并的媒体流"""
    headers = f"User-Agent: {WECHAT_UA}\r\n"
    if referer:
        headers += f"Referer: {referer}\r\n"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-headers",
        headers,
        "-i",
        url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-y",
        str(save_path),
    ]
    try:
        print(f"用 ffmpeg 下载/合并: {save_path.name}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0 and save_path.exists() and save_path.stat().st_size > 0:
            print(f"ffmpeg 下载完成: {save_path}")
            return True
        print(f"ffmpeg 下载失败: {(result.stderr or result.stdout)[-1000:]}")
        return False
    except FileNotFoundError:
        print("ffmpeg 未安装，无法下载 m3u8/HLS 回放流")
        return False
    except subprocess.TimeoutExpired:
        print("ffmpeg 下载超时")
        return False


def download_media_url(url: str, save_path: Path, referer: str = "") -> bool:
    lower = urlparse(url).path.lower()
    if ".m3u8" in lower or "m3u8" in url.lower():
        return download_with_ffmpeg(url, save_path, referer=referer)
    return download_with_requests(url, save_path, referer=referer)


def extract_video_url_from_workers_data(data: dict) -> str:
    feed = data.get("feedInfo", {}) if isinstance(data, dict) else {}
    candidates = [
        feed.get("videoUrl"),
        feed.get("h264Url"),
        feed.get("h265Url"),
        feed.get("mediaUrl"),
        feed.get("url"),
        feed.get("h264VideoInfo", {}).get("videoUrl") if isinstance(feed.get("h264VideoInfo"), dict) else "",
        feed.get("h265VideoInfo", {}).get("videoUrl") if isinstance(feed.get("h265VideoInfo"), dict) else "",
    ]
    for item in candidates:
        if item:
            return normalize_media_url(item)
    return ""


def collect_media_urls(obj, found: Optional[set] = None) -> set:
    """递归提取响应 JSON 里的 mp4/m3u8/播放 URL"""
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = key.lower()
            if isinstance(value, str):
                value = normalize_media_url(value)
                value_l = value.lower()
                if (
                    value_l.startswith("http")
                    and ("video" in key_l or "url" in key_l or "play" in key_l or "hls" in key_l)
                    and any(mark in value_l for mark in [".mp4", ".m3u8", "finder.video.qq.com", "wxvideo"])
                ):
                    found.add(value)
            else:
                collect_media_urls(value, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_media_urls(item, found)
    return found


def candidate_sort_key(url: str) -> tuple:
    lower = url.lower()
    # 直播回放常见 m3u8 优先，其次 mp4；保留完整 query，签名参数不能丢。
    return (
        0 if ".m3u8" in lower else 1 if ".mp4" in lower else 2,
        -len(url),
    )


def try_official_preview_api(share_link: str, save_dir: Path) -> Optional[dict]:
    """使用微信官方预览接口探测网页可见的播放地址，兼容直播回放的 dynamicExportId"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright 未安装，跳过官方预览接口")
        return None

    short_uri = get_short_uri(share_link)
    if not short_uri:
        return None

    print("尝试通过微信官方预览接口解析...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=WECHAT_UA)
        page = context.new_page()
        page.goto(share_link, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        result = page.evaluate(
            """async ({shortUri}) => {
                const api = '/finder-preview/api/feed/get_feed_info?_rid=codex'
                    + '&_pageUrl=https:%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Fsph';
                async function post(data) {
                    const resp = await fetch(api, {
                        method: 'POST',
                        headers: {'content-type': 'application/json'},
                        body: JSON.stringify(data)
                    });
                    const text = await resp.text();
                    let json = null;
                    try { json = JSON.parse(text); } catch (e) {}
                    return {status: resp.status, text, json};
                }
                const first = await post({baseReq: {generalToken: ''}, shortUri});
                const eid = first?.json?.data?.sceneInfo?.dynamicExportId;
                let second = null;
                if (eid) {
                    second = await post({baseReq: {generalToken: ''}, exportId: eid});
                }
                return {pageUrl: location.href, first, second, eid};
            }""",
            {"shortUri": short_uri},
        )
        browser.close()

    json_objects = []
    for key in ["first", "second"]:
        item = result.get(key) or {}
        if item.get("json"):
            json_objects.append(item["json"])

    media_urls = set()
    for obj in json_objects:
        media_urls.update(collect_media_urls(obj))

    if media_urls:
        video_url = sorted(media_urls, key=candidate_sort_key)[0]
        data = (result.get("second") or result.get("first") or {}).get("json", {}).get("data", {})
        feed = data.get("feedInfo", {}) if isinstance(data, dict) else {}
        author = data.get("authorInfo", {}).get("nickname", "未知作者") if isinstance(data, dict) else "未知作者"
        title = feed.get("description") or feed.get("nickname") or f"video_{short_uri}"
        filename = safe_filename(title, f"video_{short_uri}") + ".mp4"
        save_path = save_dir / filename
        if download_media_url(video_url, save_path, referer=result.get("pageUrl") or share_link):
            return {"path": save_path, "author": author or "未知作者", "title": title}
        return None

    err = None
    for key in ["second", "first"]:
        data = ((result.get(key) or {}).get("json") or {}).get("data", {})
        msg = data.get("errMsg") if isinstance(data, dict) else None
        if msg:
            err = msg.get("title") or msg.get("wording")
            break
    if result.get("eid"):
        print(f"已识别微信客户端专属内容 dynamicExportId: {result['eid']}")
        if err:
            print(f"微信预览接口提示: {err}")
        raise UnsupportedReplayError(
            "该链接是微信客户端专属的直播回放/受限内容，网页预览接口没有返回 mp4/m3u8 播放地址。",
            dynamic_export_id=result["eid"],
            hint=(
                "使用本 skill 内置的微信客户端短时监听，自动提取完整媒体参数并直链下载。"
            ),
        )
    if err:
        print(f"微信预览接口提示: {err}")
    return None


def print_authorized_capture_workflow(share_link: str):
    """Explain the automatic client-assisted replay workflow."""
    print("\n这是微信客户端专属直播回放。")
    print("skill 会短时只读监听本次新建的微信网络统计日志，随后直链下载并解密。")
    print("不安装证书、不修改代理、不控制微信界面，也不进行录屏录音。")
    print(f"原始链接: {share_link}")


def try_playwright_network(share_link: str, save_dir: Path) -> Optional[dict]:
    """使用 Playwright 监听页面网络请求，提取 mp4/m3u8 播放地址"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright 未安装，跳过网络监听")
        return None

    print("尝试通过 Playwright 网络监听解析...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=WECHAT_UA)
        page = context.new_page()
        video_urls = set()

        def add_url(url: str):
            url = normalize_media_url(url)
            lower = url.lower()
            if any(mark in lower for mark in [".mp4", ".m3u8", "finder.video.qq.com", "wxvideo"]):
                video_urls.add(url)

        def on_response(response):
            add_url(response.url)
            if "get_feed_info" in response.url:
                try:
                    video_urls.update(collect_media_urls(response.json()))
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(share_link, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)

        try:
            for src in page.eval_on_selector_all(
                "video, source",
                "els => els.map(e => e.currentSrc || e.src || e.getAttribute('src')).filter(Boolean)",
            ):
                add_url(src)
        except Exception:
            pass

        html = page.content()
        for match in re.findall(r'https?://[^"\'\\\s<>]+', html):
            add_url(match)
        browser.close()

    if not video_urls:
        print("Playwright 未找到视频流地址")
        return None

    video_url = sorted(video_urls, key=candidate_sort_key)[0]
    filename = f"video_{int(time.time())}.mp4"
    save_path = save_dir / filename
    print("找到视频流地址，开始下载...")
    if download_media_url(video_url, save_path, referer=share_link):
        return {"path": save_path, "author": "", "title": save_path.stem}
    return None


def download_single_video(
    share_link: str, output_name: str = "", replay_audio_only: bool = False
) -> Optional[dict]:
    """
    下载单条视频号视频。

    策略:
    1. 腾讯元宝后台解析（普通视频）
    2. 微信官方预览接口（非 SPH 兼容路径）
    3. Playwright 网络监听（非 SPH 兼容路径）
    """
    save_dir = ensure_output_dir("videos")

    # Resolve ordinary SPH links through Yuanbao first. This uses an isolated,
    # logged-in browser profile and does not require opening or playing in WeChat.
    parsed = urlparse(share_link)
    if parsed.netloc.lower() in {"weixin.qq.com", "www.weixin.qq.com"} and "/sph/" in parsed.path:
        print("使用腾讯元宝后台接口解析目标视频...")
        try:
            from yuanbao_direct import resolve as resolve_via_yuanbao

            profile = resolve_via_yuanbao(share_link)
            filename = output_name or (
                f"{safe_filename(profile.get('title') or 'wechat-video')}"
                f"_{profile['share_id']}_{int(time.time())}.mp4"
            )
            save_path = save_dir / filename
            if download_media_url(
                profile["video_url"],
                save_path,
                referer="https://channels.weixin.qq.com/",
            ):
                return normalize_download_result(
                    {
                        "path": save_path,
                        "author": profile.get("author", ""),
                        "title": profile.get("title", ""),
                        "share_id": profile.get("share_id", ""),
                        "export_id": profile.get("export_id", ""),
                    },
                    "腾讯元宝后台接口",
                )
            print("腾讯元宝媒体下载或校验失败，切换到回放兼容流程...")
        except Exception as error:
            print(f"腾讯元宝后台解析不可用: {error}")
            if "Playwright is required" in str(error):
                print("当前 Python 环境无法加载 Playwright，不能判断为直播回放。")
                print("请先修复依赖或使用 scripts\\run.cmd 执行；本次不会要求打开微信。")
                return None

        print("该链接可能是客户端专属直播回放，准备两阶段只读捕获。")
        print("本流程不会点击、控制或读取微信界面，也不安装代理或证书。")
        from replay_capture import ReplayActionRequired, arm_replay

        arm_replay(share_link)
        raise ReplayActionRequired(share_link)

    strategies = [
        ("微信官方预览接口", lambda: try_official_preview_api(share_link, save_dir)),
        ("Playwright 网络监听", lambda: try_playwright_network(share_link, save_dir)),
    ]

    for name, func in strategies:
        print(f"\n--- 尝试 {name} ---")
        try:
            result = func()
        except UnsupportedReplayError as e:
            print(str(e))
            if e.dynamic_export_id:
                print(f"dynamicExportId: {e.dynamic_export_id}")
            print("客户端访问已禁用，任务安全停止。")
            return None
        if result:
            validated = normalize_download_result(result, name)
            if validated:
                return validated
        print(f"{name} 未获取到视频")

    print("\n纯后台下载方式均失败；客户端访问已禁用，任务安全停止。")
    print(f"视频保存目录: {save_dir}")
    return None


def download_multiple_videos(links_file: str) -> list:
    """
    批量下载视频（从文本文件读取链接列表）
    每行一个分享链接
    """
    file_path = Path(links_file)

    if not file_path.exists():
        print(f"文件不存在: {links_file}")
        return []

    links = file_path.read_text(encoding="utf-8").strip().splitlines()
    links = [line.strip() for line in links if line.strip()]

    print(f"共 {len(links)} 个视频链接")
    downloaded = []

    for i, link in enumerate(links, 1):
        print(f"\n[{i}/{len(links)}] 处理: {link[:80]}...")
        result = download_single_video(link, f"video_{i:03d}")
        if result:
            downloaded.append(result)

    print(f"\n下载完成: {len(downloaded)}/{len(links)} 个视频")
    return downloaded


def get_video_files(directory: Optional[Path] = None) -> list:
    """获取指定目录下的视频文件"""
    if directory is None:
        directory = ensure_output_dir("videos")
    video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
    files = []
    for f in directory.iterdir():
        if f.suffix.lower() in video_exts and f.is_file():
            files.append(f)
    return sorted(files)
