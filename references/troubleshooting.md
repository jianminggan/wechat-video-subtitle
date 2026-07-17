# 故障排查

## 目录

1. Skill 没有触发
2. Playwright 无法加载
3. 元宝要求重新登录
4. 返回封面或小文件
5. 直播回放进入第二阶段后找不到媒体
6. 直播回放抓到其他作品
7. 转写失败
8. 中文显示乱码
9. 隐私与状态清理

## Skill 没有触发

确认 Agent 实际安装目录中存在 `SKILL.md`，并重新开启一个对话。触发描述必须包含 `weixin.qq.com/sph/`。不要让 Agent 先 WebFetch、Web Search、Computer Use 或调用 yt-dlp。

命令必须使用 Skill 的绝对路径。Claude Code 不要在 Bash 中写 Windows 反斜杠相对路径；使用 PowerShell：

```powershell
& "$HOME\.claude\skills\wechat-video-subtitle\scripts\run.cmd" download "<链接>"
```

## Playwright 无法加载

先确认 Agent 实际调用的 Python：

```powershell
python -c "import sys; print(sys.executable); import playwright; print(playwright.__file__)"
```

再安装到同一个解释器：

```powershell
python -m pip install -r scripts\requirements.txt
python -m playwright install chromium
```

不要因为 Playwright 缺失就进入直播回放流程。它是环境故障，不是内容类型判断依据。

## 元宝要求重新登录

运行：

```powershell
scripts\run.cmd auth
```

使用弹出的独立浏览器完成登录。不要把浏览器配置目录复制到其他设备或上传 GitHub；每台设备应独立登录。

如果提示 profile 已被占用，关闭之前由 `auth` 打开的元宝 Chromium 窗口，再重试。

## 返回封面或小文件

有效视频必须同时满足：

- 大小默认超过 1 MiB；
- 不是 JPEG/PNG/GIF/HTML/XML 文件头；
- MP4 包含 `ftyp`；
- ffprobe 能发现视频流。

校验失败时脚本会删除伪视频并返回失败。不要仅凭扩展名 `.mp4` 或“请求成功”报告完成。

## 直播回放进入第二阶段后找不到媒体

1. 确认使用 Windows 桌面微信，而不是浏览器或手机。
2. 第一阶段完成后再重新打开目标链接；旧窗口中的历史播放不会被读取。
3. 播放几秒，看到画面开始推进后再运行 `NEXT_COMMAND`。
4. 准备状态默认 30 分钟过期；过期后重新执行 `replay-arm`。
5. 不需要退出微信、重新登录、清缓存或扫码。

## 直播回放抓到其他作品

关闭旧的视频号播放窗口通常有助于减少干扰，但不是强制要求。必须在第一阶段之后重新打开目标链接，并实际让目标回放开始推进。脚本根据分享会话、活跃 `feedId` 和文件总大小选择目标；校验不一致时应失败，不能退回下载旧缓存。

## 转写失败

检查 `ASR_API_KEY`、`ASR_BASE_URL`、`ASR_MODEL` 是否属于同一个 OpenAI-compatible 接口，并确认接口支持 `chat/completions` 的 `input_audio` 输入。

长视频默认分成连续 300 秒片段，并发 4 个请求。遇到限流时降低 `ASR_WORKERS`；接口限制单次音频大小时降低 `ASR_SEGMENT_SECONDS`，但不建议低于 60 秒。

云端 ASR 不可用时可设置 `USE_WHISPER=true` 使用本地 Whisper，但需要额外安装对应 Python 包和模型。

## 中文显示乱码

`run.cmd` 会设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`。如果某个 Agent 的工具面板仍显示乱码，先直接打开生成的 `.md` 文件核对；不要把终端渲染错误当作文件损坏。

## 隐私与状态清理

本机状态目录：

```text
~/.wechat-video-subtitle/
  yuanbao/profile/
  replay-state/
```

删除 `yuanbao/profile` 会清除元宝独立登录状态，下次需要重新登录。删除 `replay-state` 只会清除未完成的直播回放准备状态。执行清理前确认没有正在运行的任务。
