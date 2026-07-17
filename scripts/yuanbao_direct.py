"""Direct WeChat Channels share-link resolver using a Yuanbao web session.

This module is intentionally independent from the existing WeChat client capture
path so it can be validated before it becomes the skill's default downloader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import site
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse


PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
FEED_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
STATE_DIR = Path.home() / ".wechat-video-subtitle" / "yuanbao"
PROFILE_DIR = STATE_DIR / "profile"
VALIDATION_DIR = Path.home() / "Downloads" / "stt_output" / "_direct-validation"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _restore_dependency_paths() -> None:
    candidates: list[str] = []
    try:
        candidates.append(site.getusersitepackages())
    except (AttributeError, RuntimeError):
        pass
    appdata = Path.home() / "AppData" / "Roaming" / "Python"
    if appdata.is_dir():
        for child in sorted(appdata.iterdir(), reverse=True):
            sp = child / "site-packages"
            if sp.is_dir():
                candidates.append(str(sp))
    # Append to END of sys.path so venv-installed packages take priority
    for candidate in candidates:
        if candidate and candidate not in sys.path and Path(candidate).exists():
            sys.path.append(candidate)


_restore_dependency_paths()

import requests


class YuanbaoAuthRequired(RuntimeError):
    pass


class DirectResolveError(RuntimeError):
    pass


def _load_playwright():
    _restore_dependency_paths()
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            f"Playwright is required for Yuanbao link parsing "
            f"(python={sys.executable}, user_site={site.getusersitepackages()})"
        ) from error
    return PlaywrightError, sync_playwright


def _safe_filename(value: str, default: str = "wechat-video") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip(" ._")
    return (value[:120] or default).strip()


def _share_id(share_url: str) -> str:
    parsed = urlparse(share_url)
    if parsed.netloc.lower() not in {"weixin.qq.com", "www.weixin.qq.com"}:
        raise DirectResolveError("Only weixin.qq.com share links are supported")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[-2].lower() != "sph":
        raise DirectResolveError("The URL is not a weixin.qq.com/sph share link")
    if not re.fullmatch(r"[A-Za-z0-9]+", parts[-1]):
        raise DirectResolveError("Invalid SPH share id")
    return parts[-1]


def authenticate(timeout_seconds: int = 300) -> None:
    try:
        _, sync_playwright = _load_playwright()
    except RuntimeError as error:
        raise RuntimeError("Playwright is required for Yuanbao authentication") from error

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    print("Opening a dedicated Tencent Yuanbao login window...")
    print("Authentication is needed only for first use or after the session expires.")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            user_agent=USER_AGENT,
            viewport={"width": 1200, "height": 850},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            body = page.locator("body").inner_text(timeout=5000)
            if "未登录" not in body and ("元宝" in body or "新建对话" in body):
                context.close()
                print("Yuanbao authentication is active in the dedicated profile.")
                return
            page.wait_for_timeout(1000)
        context.close()
    raise YuanbaoAuthRequired("Yuanbao login timed out")


def _browser_parse_payload(share_url: str) -> dict[str, Any]:
    PlaywrightError, sync_playwright = _load_playwright()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=True,
                user_agent=USER_AGENT,
                viewport={"width": 1200, "height": 850},
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=60000)
                result = page.evaluate(
                    """async ({endpoint, shareUrl}) => {
                        const response = await fetch(endpoint, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'content-type': 'application/json'},
                            body: JSON.stringify({type: 'video_channel_url', url: shareUrl, scene: 1})
                        });
                        return {status: response.status, text: await response.text()};
                    }""",
                    {"endpoint": PARSE_URL, "shareUrl": share_url},
                )
            finally:
                context.close()
    except PlaywrightError as error:
        message = str(error)
        if "ProcessSingleton" in message or "profile" in message.lower():
            raise DirectResolveError("The dedicated Yuanbao profile is already in use") from error
        raise DirectResolveError(f"Yuanbao browser request failed: {message}") from error

    status = int(result.get("status") or 0)
    if status in {401, 403}:
        raise YuanbaoAuthRequired(f"Yuanbao session rejected (HTTP {status})")
    if status != 200:
        raise DirectResolveError(f"Yuanbao returned HTTP {status}")
    try:
        return json.loads(str(result.get("text") or ""))
    except json.JSONDecodeError as error:
        raise DirectResolveError("Yuanbao returned a non-JSON response") from error


def parse_share_url(share_url: str) -> dict[str, Any]:
    _share_id(share_url)
    payload = _browser_parse_payload(share_url)
    if payload.get("code") not in (0, None):
        message = str(payload.get("msg") or f"code={payload.get('code')}")
        if any(word in message.lower() for word in ("login", "cookie", "登录", "鉴权")):
            raise YuanbaoAuthRequired(f"Yuanbao session expired: {message}")
        raise DirectResolveError(f"Yuanbao parse failed: {message}")
    data = payload.get("data") or {}
    playable_url = str(data.get("playable_url") or "")
    query = parse_qs(urlparse(playable_url).query)
    token = (query.get("token") or [""])[0]
    export_id = (query.get("eid") or [""])[0]
    if not token or not export_id:
        raise DirectResolveError("Yuanbao response is missing token or eid")
    return {
        "token": token,
        "export_id": export_id,
        "title": str(data.get("desc") or ""),
        "author": str(data.get("author") or ""),
        "cover_url": str(data.get("cover_url") or ""),
    }


def get_feed_info(export_id: str, token: str, session: requests.Session | None = None) -> dict[str, Any]:
    rid = f"{int(time.time()):x}-{random.randrange(16**8):08x}"
    page_url = "https://channels.weixin.qq.com/finder-preview/pages/feed"
    endpoint = f"{FEED_URL}?_rid={rid}&_pageUrl={quote(page_url, safe='')}"
    referer = (
        f"{page_url}?entry_card_type=48&comment_scene=39&appid=0"
        f"&token={quote(token, safe='')}&entry_scene=0&eid={quote(export_id, safe='')}"
    )
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://channels.weixin.qq.com",
        "referer": referer,
        "user-agent": USER_AGENT,
    }
    client = session or requests.Session()
    try:
        response = client.post(
            endpoint,
            json={"baseReq": {"generalToken": token}, "exportId": export_id},
            headers=headers,
            timeout=(15, 30),
        )
    except requests.RequestException as error:
        raise DirectResolveError(f"Feed request failed: {error}") from error
    if not 200 <= response.status_code < 300:
        raise DirectResolveError(f"Feed endpoint returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except requests.JSONDecodeError as error:
        raise DirectResolveError("Feed endpoint returned a non-JSON response") from error
    if payload.get("errCode") not in (0, None):
        raise DirectResolveError(str(payload.get("errMsg") or f"errCode={payload.get('errCode')}"))
    data = payload.get("data") or {}
    feed = data.get("feedInfo") or {}
    if not feed:
        error_info = data.get("errMsg") or {}
        raise DirectResolveError(str(error_info.get("title") or "Feed response has no media data"))
    return payload


def resolve(share_url: str) -> dict[str, Any]:
    session = requests.Session()
    parsed = parse_share_url(share_url)
    feed_payload = get_feed_info(parsed["export_id"], parsed["token"], session=session)
    data = feed_payload.get("data") or {}
    feed = data.get("feedInfo") or {}
    author = data.get("authorInfo") or {}
    candidates = []
    for key in ("videoUrl", "originVideoUrl"):
        if feed.get(key):
            candidates.append(feed[key])
    for key in ("h264VideoInfo", "h265VideoInfo"):
        info = feed.get(key) or {}
        if info.get("videoUrl"):
            candidates.append(info["videoUrl"])
    video_url = next((str(item) for item in candidates if str(item).startswith("http")), "")
    if not video_url:
        raise DirectResolveError("The resolved content has no downloadable video URL")
    return {
        "share_id": _share_id(share_url),
        "export_id": parsed["export_id"],
        "title": str(feed.get("description") or parsed["title"] or "wechat-video"),
        "author": str(author.get("nickname") or parsed["author"] or ""),
        "cover_url": str(feed.get("coverUrl") or parsed["cover_url"] or ""),
        "media_type": feed.get("mediaType"),
        "create_time": feed.get("createtime"),
        "video_url": video_url,
        "url_fingerprint": hashlib.sha256(video_url.encode("utf-8")).hexdigest()[:16],
    }


def download(share_url: str, output_root: Path = VALIDATION_DIR) -> dict[str, Any]:
    profile = resolve(share_url)
    task_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    task_dir = output_root / profile["share_id"] / task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    title = _safe_filename(profile["title"], f"video-{profile['share_id']}")
    part_path = task_dir / f"{title}.mp4.part"
    final_path = task_dir / f"{title}.mp4"
    headers = {"user-agent": USER_AGENT, "referer": "https://channels.weixin.qq.com/"}
    try:
        with requests.get(
            profile["video_url"], headers=headers, stream=True, timeout=(20, 180)
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "image/" in content_type or "text/html" in content_type:
                raise DirectResolveError(f"CDN returned invalid content type: {content_type}")
            with part_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        from download import validate_video_file

        valid, reason = validate_video_file(part_path)
        if not valid:
            raise DirectResolveError(f"Downloaded media validation failed: {reason}")
        part_path.replace(final_path)
        result = {**profile, "path": str(final_path), "task_id": task_id, "bytes": final_path.stat().st_size}
        metadata = {
            key: value
            for key, value in result.items()
            if key not in {"video_url", "cover_url"}
        }
        (task_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
    except Exception:
        part_path.unlink(missing_ok=True)
        raise


def _redacted_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in profile.items()
        if key not in {"video_url", "cover_url"}
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Direct WeChat Channels SPH resolver")
    subparsers = parser.add_subparsers(dest="command", required=True)
    auth_parser = subparsers.add_parser("auth")
    auth_parser.add_argument("--timeout", type=int, default=300)
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("url")
    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("url")
    download_parser.add_argument("--output-root", type=Path, default=VALIDATION_DIR)
    args = parser.parse_args(argv)
    try:
        if args.command == "auth":
            authenticate(args.timeout)
            return 0
        if args.command == "probe":
            print(json.dumps(_redacted_profile(resolve(args.url)), ensure_ascii=False, indent=2))
            return 0
        result = download(args.url, output_root=args.output_root)
        print(json.dumps(_redacted_profile(result), ensure_ascii=False, indent=2))
        return 0
    except YuanbaoAuthRequired as error:
        print(f"AUTH_REQUIRED: {error}", file=sys.stderr)
        return 3
    except (DirectResolveError, requests.RequestException) as error:
        print(f"DIRECT_RESOLVE_FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
