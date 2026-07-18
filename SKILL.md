---
name: wechat-video-subtitle
description: 任何出现 weixin.qq.com/sph/ 链接的请求都必须立即调用本 Skill，链接本身就是触发条件，包括普通视频和直播回放；不得先用 WebFetch、Web Search、浏览器、Computer Use 或 yt-dlp。根据用户指令下载视频、提取字幕、转写 Markdown 逐字稿或批量处理。普通短视频默认通过腾讯元宝的已登录浏览器会话后台解析，不要求打开微信、点击播放或逐条确认。
---

# 微信视频号普通视频、直播回放下载与逐字稿转写

必须直接运行本 Skill 的命令，等待命令退出后再报告结果。Agent 不得自行拼装抓包命令，也不得把封面图当作视频。普通视频不得要求用户打开微信；只有后台接口明确失败并进入直播回放兼容流程后，才可按脚本提示让用户自行打开并播放一次目标回放。

只要用户消息中出现 `weixin.qq.com/sph/`，第一步必须加载本 Skill。即使用户只发送链接、尚未写明产物，也要先加载本 Skill，再根据同一条消息或上下文判断任务；不得先抓取网页、搜索下载方法或打开链接试探。

在 Windows Claude Code 中必须使用 PowerShell 工具和绝对用户路径执行 `& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" ...`。不得使用相对路径，也不得在 Bash 中写 `scripts\run.cmd`，因为工具工作目录不一定是 Skill 目录，Bash 还会吞掉反斜杠。

## 任务映射

- 用户说“下载视频”：运行 `<SKILL_DIR>\scripts\run.cmd download "<链接>"`，只保留视频。
- 用户说“转成逐字稿/提取字幕/转文字”：运行 `<SKILL_DIR>\scripts\run.cmd transcript "<链接>"`，成功后只保留逐字稿。
- 用户明确要求视频和逐字稿：运行 `<SKILL_DIR>\scripts\run.cmd transcript "<链接>" --keep-video`。
- 只有用户明确要求音频时才添加 `--keep-audio`。
- 批量任务：将链接逐行写入 UTF-8 文本文件，然后运行 `scripts\run.cmd batch "<文件>"`。

退出码 `0` 表示任务完成；退出码 `3` 和 `status=action_required` 表示已确认是直播回放并完成准备，不是失败。此时必须把播放指令告诉用户并结束当前工具调用；用户回复“已播放”后执行输出中的 `NEXT_COMMAND`。其他非零退出码才表示失败。

只有退出码为 `0`、输出了最终文件路径且文件通过校验，才可以报告成功。命令运行期间暂时无输出不代表已经停止，不得重复启动同一任务。

## 首次安装

```powershell
python -m pip install -r scripts/requirements.txt
python -m playwright install chromium
scripts\run.cmd auth
```

`auth` 使用独立浏览器配置 `~/.wechat-video-subtitle/yuanbao/profile`。首次使用或登录过期时，用户只需在弹出的腾讯元宝窗口登录一次。日常下载不需要退出重登，不需要打开微信，也不需要播放视频。

## 日常命令

```powershell
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" download "https://weixin.qq.com/sph/..."
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" transcript "https://weixin.qq.com/sph/..."
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" transcript "https://weixin.qq.com/sph/..." --keep-video
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" batch links.txt
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" transcribe "C:\videos\local.mp4"
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" replay-arm "https://weixin.qq.com/sph/..."
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" replay-download "https://weixin.qq.com/sph/..."
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" replay-transcript "https://weixin.qq.com/sph/..."
```

Hermes 使用 `$env:LOCALAPPDATA\hermes\skills\wechat-video-subtitle\scripts\run.cmd`；其他 Agent 使用其加载 Skill 时显示的绝对 `Base directory`。不要假设当前工作目录就是 Skill 目录。

## 普通视频自动流程

对 `weixin.qq.com/sph/` 链接固定执行以下流程：

1. 使用独立的腾讯元宝 Chromium 配置在无头模式打开元宝。
2. 在元宝同源页面内调用解析接口，避免错误地依赖 Cookie 文件。
3. 从本次响应提取 `token` 和 `eid`，再调用视频号预览接口取得标题、作者和媒体地址。
4. 每个链接重新解析，不复用上一条的媒体地址；下载文件名包含分享 ID 和任务时间。
5. 下载后校验 Content-Type、文件大小、MP4 `ftyp` 和 ffprobe 视频流。
6. 校验失败即返回失败，不得降级为封面图或上一条视频。

这条路径不启动微信、不播放视频、不按视频时长录制，也不使用 mitmproxy、证书注入或手动抓包。

## 直播回放兼容流程

腾讯元宝并非对所有直播回放返回媒体凭据。后台解析失败后必须采用两阶段协议，禁止在一条阻塞命令中等待用户操作：

1. 普通 `download/transcript` 后台解析失败时自动写入仅含分享 ID 和准备时间的状态，输出 `ACTION_REQUIRED`、`NEXT_COMMAND`，并以退出码 `3` 立即返回。
2. Agent 告诉用户：现在在已登录的 Windows 微信中重新打开目标链接并播放几秒，然后回复“已播放”。Agent 必须等待用户回复，不得在同一轮立刻执行下一条命令。
3. 用户回复后，下载任务运行 `replay-download`；逐字稿任务运行 `replay-transcript`，并保留原任务的 `--keep-video/--keep-audio` 参数。
4. 第二阶段只读取准备时间之后产生的 `Tencent\xwechat\net*\kvcomm` 网络统计记录，用分享 ID 锁定会话。
5. 用正在推进的 `feedId`、播放事件和 `Content-Range` 总大小选择主播放流，拒绝同页预加载的其他作品。
6. 从同一会话字段自动匹配 ISAAC64 解密参数；状态文件不得包含签名 URL、token 或解密密钥。
7. `replay-download` 流式解密并保存 MP4；`replay-transcript` 直接提取临时音频，完成转写后按默认规则清理。

如果用户一开始就明确说“直播回放”，可直接运行 `replay-arm <链接>` 跳过元宝探测；等待用户回复“已播放”后再运行对应的第二阶段命令。准备状态默认 30 分钟过期。

严禁 Agent 打开、点击、控制或读取微信界面，严禁向文件传输助手、联系人或群聊发送链接或文字。不得安装本地证书、修改系统代理、运行 mitmproxy、扫描进程内存或调用旧的 `wechat_sniff.py`、`wechat_client_capture.py`。直播回放允许的唯一人工动作是用户自行重新打开并播放目标链接；准备过期后安全停止，不要求扫码、清缓存或重登。

普通视频和普通视频批量任务继续走元宝纯后台流程，不需要微信。直播回放依赖一次人工播放，因此不能承诺完全无人值守的批量下载。

## 输出规则

```text
~/Downloads/WeChat-video-download/
  <标题>/
    video/<标题>.mp4
    transcript/<标题>.md
    audio/chunk_000.mp3
```

所有产物位于同一个任务根目录。`download` 只保留 `video/`；`transcript` 默认在成功后删除中间视频和音频；`--keep-video` 或 `--keep-audio` 才保留对应目录。转写失败时保留中间文件以便续跑。

下载器必须拒绝：小于 1 MiB 的文件、JPEG/PNG/GIF、HTML/XML、没有 MP4 `ftyp` 的文件，以及 ffprobe 未发现视频流的文件。

## 逐字稿规则

音频默认切成连续的 5 分钟片段，并发识别 4 段。片段首尾连续，不留空隙；可用 `ASR_SEGMENT_SECONDS` 调整，但不得低于 60 秒。

逐字稿保留原始口语内容，纠正常见同音字、错别字和术语，补充标点并按语义分段；默认不添加摘要。时间戳或额外结构以用户指令为准。

## 运行环境

1. 普通视频和本地转写需要 Python 3.10+；直播回放兼容流程额外要求 Windows 10/11。
2. `scripts/requirements.txt` 中的依赖和 Playwright Chromium。
3. FFmpeg/ffprobe 位于 `PATH`。
4. 转写任务配置 `ASR_API_KEY`、`ASR_BASE_URL`、`ASR_MODEL`、`ASR_API_MODE`；可放在项目 `.env`、`~/.wechat-video-subtitle/.env` 或系统环境变量中。MiMo 使用 `chat`，OpenAI/SiliconFlow 标准转写接口使用 `transcriptions`。
5. 普通视频需要腾讯元宝一次性登录；不需要微信客户端。回放兼容流程才可能依赖已登录的 Windows 微信。

环境变量：`ASR_SEGMENT_SECONDS` 默认 `300`；`USE_WHISPER=true` 与 `WHISPER_MODEL` 可切换本地 Whisper；`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 可用于逐字稿纠错；`WECHAT_MIN_VIDEO_BYTES` 默认 `1048576`；`WECHAT_OUTPUT_DIR` 可修改输出根目录。

遇到依赖、登录、回放捕获、串片或乱码问题时，读取 `references/troubleshooting.md`，不要自行改走网页搜索、微信 UI 自动化、代理抓包或录屏方案。

仅处理用户有权查看、保存或转写的内容。不要在日志或最终产物中输出媒体签名 URL、登录状态数据、鉴权参数或解密密钥。
