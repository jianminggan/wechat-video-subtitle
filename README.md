# wechat-video-subtitle

面向 Codex、Claude Code、Hermes 等 AI Agent 的微信视频号下载与逐字稿 Skill。

给 Agent 一个 `https://weixin.qq.com/sph/...` 链接，并说明“下载视频”或“提取逐字稿”即可。普通短视频走腾讯元宝后台解析，日常使用不需要打开微信；直播回放在 Windows 上使用微信客户端的本地网络统计记录，需要用户手动打开目标回放并播放几秒，但不录屏、不录音，也不安装代理或证书。

## 能力与限制

| 场景 | 自动化程度 | Windows | macOS / Linux |
| --- | --- | --- | --- |
| 普通视频下载 | 全自动，首次登录元宝一次 | 支持 | 实验性支持 |
| 普通视频转逐字稿 | 全自动 | 支持 | 实验性支持 |
| 直播回放下载 | 两阶段，用户播放几秒 | 支持 | 不支持 |
| 直播回放转逐字稿 | 两阶段，用户播放几秒 | 支持 | 不支持 |
| 本地视频转逐字稿 | 全自动 | 支持 | 支持 |

直播回放依赖 Windows 微信客户端产生的 `Tencent/xwechat/net*` 本地网络统计记录，因此当前不能在 macOS/Linux 上工作。普通视频不依赖微信客户端。

## 工作原理

### 普通视频

1. 使用独立 Chromium 配置打开腾讯元宝的已登录会话。
2. 在元宝同源页面内提交当前分享链接，每个任务都重新解析。
3. 使用本次返回的临时凭据获取视频标题、作者和媒体地址。
4. 下载后检查文件大小、文件头和 `ffprobe` 视频流，拒绝把封面、HTML 错误页或上一个视频当作成功结果。

### 直播回放

1. 第一阶段只记录分享 ID 和准备时间，然后返回 `ACTION_REQUIRED`。
2. 用户在已登录的 Windows 微信中重新打开目标链接并播放几秒。
3. 第二阶段只读取准备时间之后新产生的微信本地网络统计记录。
4. 按分享会话、`feedId`、播放活动和 `Content-Range` 选择主播放流，避免抓到同页预加载的其他作品。
5. 自动匹配解密参数，流式解密并保存 MP4，或直接提取临时音频生成逐字稿。

这个流程不会控制或读取微信界面，不会给联系人发消息，不安装本地证书，不修改系统代理，不运行 mitmproxy，也不扫描微信进程内存。

## 环境要求

- Python 3.10 或更高版本
- FFmpeg 与 ffprobe，且可从 `PATH` 运行
- Playwright Chromium
- 普通视频：腾讯元宝账号，首次使用登录一次
- 直播回放：Windows 10/11、已登录的桌面微信
- 逐字稿：OpenAI-compatible ASR 接口，或本地 Whisper

### 安装 FFmpeg

Windows 可使用：

```powershell
winget install Gyan.FFmpeg
```

macOS 可使用：

```bash
brew install ffmpeg
```

## 安装 Skill

### 推荐：一个主目录，多 Agent 共用

```powershell
git clone https://github.com/jianminggan/wechat-video-subtitle.git "$HOME\.agents\skills\wechat-video-subtitle"
```

Codex 默认能从 `.agents/skills` 发现 Skill。Claude Code 和 Hermes 可创建目录联接，三者始终使用同一份代码：

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
New-Item -ItemType Junction `
  -Path "$HOME\.claude\skills\wechat-video-subtitle" `
  -Target "$HOME\.agents\skills\wechat-video-subtitle"

New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\hermes\skills" | Out-Null
New-Item -ItemType Junction `
  -Path "$env:LOCALAPPDATA\hermes\skills\wechat-video-subtitle" `
  -Target "$HOME\.agents\skills\wechat-video-subtitle"
```

如果目标目录已经存在，请先确认里面没有要保留的自定义修改，再自行重命名或移走；不要直接覆盖。

只使用一个 Agent 时，也可以把仓库直接克隆到对应目录：

- Codex：`%USERPROFILE%\.codex\skills\wechat-video-subtitle`
- Claude Code：`%USERPROFILE%\.claude\skills\wechat-video-subtitle`
- Hermes：`%LOCALAPPDATA%\hermes\skills\wechat-video-subtitle`

### 安装 Python 依赖

在仓库目录运行：

```powershell
python -m pip install -r scripts\requirements.txt
python -m playwright install chromium
```

首次使用普通视频下载时登录腾讯元宝：

```powershell
scripts\run.cmd auth
```

弹出的浏览器使用独立配置目录 `~/.wechat-video-subtitle/yuanbao/profile`。登录成功后关闭即可，平时无需重复登录。

## 配置逐字稿

复制 `.env.example` 为 `.env`，填入自己的接口信息。`.env` 已被 Git 忽略，不会提交。

```dotenv
ASR_API_KEY=your-key
ASR_BASE_URL=https://your-provider.example/v1
ASR_MODEL=your-asr-model
```

也可以把配置放在 `~/.wechat-video-subtitle/.env`，或直接设置系统环境变量。环境变量优先级最高。

可选的 LLM 纠错配置：

```dotenv
LLM_API_KEY=your-key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model
```

默认把音频切为连续的 5 分钟片段，并发识别 4 段。片段之间不留空隙，因此不会主动丢掉衔接处；如接口限制更严格，可调整：

```dotenv
ASR_SEGMENT_SECONDS=300
ASR_WORKERS=4
```

不使用云端 ASR 时，可安装并启用本地 Whisper：

```dotenv
USE_WHISPER=true
WHISPER_MODEL=large
```

## 直接运行

Windows：

```powershell
scripts\run.cmd download "https://weixin.qq.com/sph/..."
scripts\run.cmd transcript "https://weixin.qq.com/sph/..."
scripts\run.cmd transcript "https://weixin.qq.com/sph/..." --keep-video
scripts\run.cmd batch links.txt
scripts\run.cmd transcribe "D:\videos\demo.mp4"
```

macOS/Linux 的普通视频与本地转写：

```bash
sh scripts/run.sh download "https://weixin.qq.com/sph/..."
sh scripts/run.sh transcript "https://weixin.qq.com/sph/..."
```

直播回放可显式执行：

```powershell
scripts\run.cmd replay-arm "https://weixin.qq.com/sph/..."
# 在 Windows 微信中重新打开目标链接并播放几秒
scripts\run.cmd replay-download "https://weixin.qq.com/sph/..."
```

只要用户明确说“直播回放”，Agent 应直接使用 `replay-arm`。如果未说明类型，普通 `download`/`transcript` 会先尝试元宝后台解析，确认无法取得媒体后再进入回放流程。

## 对 Agent 下指令

常用提示词：

```text
https://weixin.qq.com/sph/xxxx 下载这个视频
https://weixin.qq.com/sph/xxxx 提取逐字稿
https://weixin.qq.com/sph/xxxx 生成逐字稿，同时保留视频
```

Skill 的触发描述明确要求：看到 `weixin.qq.com/sph/` 就先加载本 Skill，不先 WebFetch、搜索、打开浏览器或尝试 yt-dlp。不同模型的工具调用能力仍有差异，但下载、校验、转写和清理逻辑都在脚本中完成，模型只负责选择命令和报告结果。

## 输出规则

默认输出到 `~/Downloads/stt_output`：

```text
stt_output/
  视频标题/
    video/视频标题.mp4
    transcript/视频标题.md
    audio/chunk_000.mp3
```

- `download`：只保留视频。
- `transcript`：成功后只保留逐字稿，自动删除中间视频和音频。
- `transcript --keep-video`：保留视频和逐字稿。
- 只有显式添加 `--keep-audio` 才保留音频切片。
- 转写失败时保留中间文件，方便续跑和排查。

可用 `WECHAT_OUTPUT_DIR` 修改输出根目录。

## 我们踩过的坑

- **封面被当成视频**：仅看到“下载完成”不可信。现在强制检查大于 1 MiB、MP4 `ftyp` 和 ffprobe 视频流。
- **第二个链接仍下载第一个视频**：不能复用上一条媒体地址。现在每个链接有独立任务 ID、重新解析，并把分享 ID 与时间写入文件名。
- **普通视频误入直播回放流程**：Playwright 未安装属于环境错误，不代表链接是直播回放。脚本会明确失败，不要求用户打开微信。
- **抓到直播页面里的其他作品**：页面会预加载推荐视频。现在只读取准备时间后的记录，并综合会话、`feedId`、播放活动和完整文件大小选流。
- **Agent 重复启动同一任务**：下载或转写时长较长不等于卡死。Agent 必须等待当前命令退出，不能重复执行。
- **Hermes/Claude 的 Python 环境不同**：虚拟环境可能禁用 user site。入口会恢复依赖路径，Windows 启动器会自动寻找可用 Python。
- **中文显示乱码**：Windows 启动器设置 UTF-8；文件本身始终按 UTF-8 写入。旧终端仍乱码时，以实际 `.md` 文件为准。
- **逐字稿任务留下视频**：任务语义决定保留内容。默认只留逐字稿，只有用户明确要求才保留视频或音频。
- **把直播回放做成一条阻塞命令**：Agent 无法在等待用户操作时可靠续跑。现在使用退出码 `3` 和 `NEXT_COMMAND` 的两阶段协议。

更完整的错误解释见 [references/troubleshooting.md](references/troubleshooting.md)。

## 安全与隐私

- 仓库不包含 API Key、Cookie、元宝登录配置、微信日志、媒体签名 URL、解密参数或任何下载产物。
- 登录状态只保存在本机 `~/.wechat-video-subtitle`，该目录不属于仓库。
- 直播回放状态只记录分享 ID 和准备时间，不保存签名 URL、token 或解密密钥。
- 仅处理你有权查看、保存或转写的内容，并遵守平台条款和当地法律。

## 更新

主目录安装方式下只需：

```powershell
Set-Location "$HOME\.agents\skills\wechat-video-subtitle"
git pull
python -m pip install -r scripts\requirements.txt
```

## License

MIT。ISAAC64 的 Python 适配来源与许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
