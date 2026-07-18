# 微信视频号视频下载、直播回放下载与逐字稿转写 Skill

把一个微信视频号链接交给 AI Agent，就可以完成：

- 下载微信视频号普通短视频
- 下载微信视频号直播回放视频
- 把普通视频或直播回放转成 Markdown 逐字稿
- 批量处理多个视频号链接
- 把本地视频转成逐字稿

支持 Codex、Claude Code、Hermes 等能够运行本地命令的 AI Agent。

普通视频可以全自动下载，不需要打开微信；直播回放需要在 Windows 微信中手动播放几秒，让 Skill 获得当前回放的媒体信息。整个过程不录屏、不录音、不安装抓包证书，也不会控制或读取微信聊天界面。

## 先看结论

| 你的需求 | 是否需要元宝登录 | 是否需要 Windows 微信 | 是否需要 ASR API |
| --- | --- | --- | --- |
| 下载普通视频 | 首次使用登录一次 | 不需要 | 不需要 |
| 普通视频转逐字稿 | 首次使用登录一次 | 不需要 | 需要 |
| 下载直播回放 | 不依赖元宝结果 | 需要，手动播放几秒 | 不需要 |
| 直播回放转逐字稿 | 不依赖元宝结果 | 需要，手动播放几秒 | 需要 |
| 本地视频转逐字稿 | 不需要 | 不需要 | 需要 |

> 只下载视频，不需要配置任何 ASR API。只有“生成逐字稿”才需要语音转文字服务。

## 最简单的使用方式

安装完成后，直接对 AI Agent 说：

```text
https://weixin.qq.com/sph/xxxx 下载这个视频
https://weixin.qq.com/sph/xxxx 提取逐字稿
https://weixin.qq.com/sph/xxxx 生成逐字稿，同时保留视频
```

Skill 看到 `weixin.qq.com/sph/` 链接后会自动选择正确命令。普通视频直接后台下载；确定是直播回放时，Agent 会告诉你何时打开 Windows 微信并播放。

## 安装

### 让 AI Agent 帮你安装

把下面这段话发给 Codex、Claude Code 或 Hermes：

```text
请帮我安装这个 Skill：
https://github.com/jianminggan/wechat-video-subtitle

请按照 README 完成 Python 依赖、Playwright Chromium 和 FFmpeg 的安装，
然后运行 auth，让我登录一次腾讯元宝。
如果我要生成逐字稿，再提醒我配置 ASR API。
```

### 手动安装到多个 Agent 共用目录

推荐只保留一份主目录，再让多个 Agent 共用：

```powershell
git clone https://github.com/jianminggan/wechat-video-subtitle.git "$HOME\.agents\skills\wechat-video-subtitle"
Set-Location "$HOME\.agents\skills\wechat-video-subtitle"
python -m pip install -r scripts\requirements.txt
python -m playwright install chromium
```

Codex 默认可从 `.agents/skills` 发现 Skill。Claude Code 和 Hermes 可以创建目录联接：

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

如果同名目标目录已经存在，先确认其中没有需要保留的修改，再自行重命名或移走；不要直接覆盖。

单独安装时可使用：

| Agent | Windows 安装目录 |
| --- | --- |
| Codex | `%USERPROFILE%\.codex\skills\wechat-video-subtitle` |
| Claude Code | `%USERPROFILE%\.claude\skills\wechat-video-subtitle` |
| Hermes | `%LOCALAPPDATA%\hermes\skills\wechat-video-subtitle` |

## 前置条件

| 依赖 | 用途 | 是否必需 |
| --- | --- | --- |
| Python 3.10+ | 运行 Skill | 必需 |
| FFmpeg / ffprobe | 校验视频、提取和切分音频 | 必需 |
| Playwright Chromium | 登录元宝并解析普通视频 | 普通视频必需 |
| 腾讯元宝登录 | 获取普通视频媒体信息 | 普通视频首次使用配置 |
| Windows 桌面微信 | 获取直播回放媒体信息 | 仅直播回放需要 |
| ASR API 或本地 Whisper | 语音转文字 | 仅逐字稿需要 |

Windows 安装 FFmpeg：

```powershell
winget install Gyan.FFmpeg
```

macOS 安装 FFmpeg：

```bash
brew install ffmpeg
```

## 第一次使用：登录腾讯元宝

普通视频通过腾讯元宝后台解析。第一次使用时运行：

```powershell
scripts\run.cmd auth
```

Skill 会打开一个独立的腾讯元宝 Chromium 窗口。使用微信扫码或元宝支持的其他方式完成登录即可。

需要注意：

- 这是腾讯元宝登录，不是“视频号助手”登录。
- 一台电脑通常只需登录一次；登录过期后再运行一次 `auth`。
- 登录状态保存在 `~/.wechat-video-subtitle/yuanbao/profile`，不会上传到 GitHub。
- 日常下载普通视频不需要打开微信，也不需要播放视频。

## 需要逐字稿时：配置 ASR API

只下载视频可以跳过本节。

复制 `.env.example` 为 `.env`，然后根据选择的服务填写。也可以把配置放在 `~/.wechat-video-subtitle/.env`，或者设置为系统环境变量。

### 方案一：小米 MiMo-V2.5-ASR，推荐

当前 Skill 默认适配 MiMo 的 `chat/completions + input_audio` 接口，中文长视频转写是最省事的配置。

- 开放平台：[platform.xiaomimimo.com](https://platform.xiaomimimo.com/)
- ASR 接口文档：[MiMo-V2.5-ASR 语音识别](https://platform.xiaomimimo.com/docs/zh-CN/api/audio/Speech-Recognition)
- API Key、Base URL 和实际费用以开放平台控制台为准

```dotenv
ASR_API_KEY=你的_MiMo_API_Key
ASR_BASE_URL=https://api.xiaomimimo.com/v1
ASR_MODEL=mimo-v2.5-asr
ASR_API_MODE=chat
```

如果购买的是 Token Plan，请使用控制台给出的专属 Base URL，例如中国集群可能是 `https://token-plan-cn.xiaomimimo.com/v1`。

### 方案二：硅基流动 SenseVoice

硅基流动提供标准 `/audio/transcriptions` 接口，可使用 SenseVoiceSmall。

- 官网：[cloud.siliconflow.cn](https://cloud.siliconflow.cn/)
- 接口文档：[Create transcription](https://docs.siliconflow.cn/cn/api-reference/audio/create-audio-transcriptions)
- 是否有免费额度、限流和价格以官网当前规则为准

```dotenv
ASR_API_KEY=你的_硅基流动_API_Key
ASR_BASE_URL=https://api.siliconflow.cn/v1
ASR_MODEL=FunAudioLLM/SenseVoiceSmall
ASR_API_MODE=transcriptions
```

### 方案三：OpenAI Speech-to-Text

可以使用 OpenAI 的标准音频转写接口。

- API 平台：[platform.openai.com](https://platform.openai.com/)
- 接口文档：[Audio transcriptions](https://platform.openai.com/docs/api-reference/audio/createTranscription)

```dotenv
ASR_API_KEY=你的_OpenAI_API_Key
ASR_BASE_URL=https://api.openai.com/v1
ASR_MODEL=gpt-4o-mini-transcribe
ASR_API_MODE=transcriptions
```

### 方案四：本地 Whisper，不使用 API

有合适显卡、愿意在本地运行模型时，可安装 OpenAI Whisper：

```powershell
python -m pip install openai-whisper
```

```dotenv
USE_WHISPER=true
WHISPER_MODEL=large
```

本地 Whisper 不产生 API 调用费用，但下载模型、显存占用和处理时间通常更高。

### 可选：逐字稿错别字纠正

ASR 会出现同音字、人名和专业术语错误。可以额外配置任意兼容 OpenAI Chat Completions 的大模型：

```dotenv
LLM_API_KEY=你的_LLM_API_Key
LLM_BASE_URL=https://你的接口地址/v1
LLM_MODEL=你的模型名称
```

后处理只纠正确定的错别字、补标点和按语义分段，不总结、不改写，保留口语表达。

## 使用方法

### 在 AI Agent 中使用

```text
下载这个微信视频号视频：https://weixin.qq.com/sph/xxxx
把这个微信视频号直播回放下载下来：https://weixin.qq.com/sph/xxxx
把这个链接转成逐字稿：https://weixin.qq.com/sph/xxxx
```

### 命令行使用

```powershell
# 只下载视频
scripts\run.cmd download "https://weixin.qq.com/sph/..."

# 只保留逐字稿，成功后自动删除中间视频和音频
scripts\run.cmd transcript "https://weixin.qq.com/sph/..."

# 同时保留视频和逐字稿
scripts\run.cmd transcript "https://weixin.qq.com/sph/..." --keep-video

# 转写本地视频
scripts\run.cmd transcribe "D:\videos\demo.mp4"

# 批量生成逐字稿，links.txt 每行一个链接
scripts\run.cmd batch links.txt
```

## 直播回放为什么需要播放几秒

腾讯元宝并不为所有直播回放返回媒体信息。遇到直播回放时，Skill 使用两阶段流程：

1. Agent 先准备任务并返回 `ACTION_REQUIRED`。
2. 你在已登录的 Windows 微信中重新打开目标直播回放并播放几秒。
3. 回复 Agent“已播放”。
4. Agent 读取这次播放新产生的本地网络统计记录，下载并校验目标回放。

Skill 不会点击或控制微信，不会读取聊天，不会给任何联系人发消息，也不要求退出微信、清缓存、重新扫码或安装抓包证书。

直播回放流程目前只支持 Windows 10/11。普通视频和本地视频转写可在 Windows 使用；macOS/Linux 的普通视频流程属于实验性支持。

## 输出规则

所有结果默认保存在：

```text
~/Downloads/WeChat-video-download/
```

每个视频有一个独立任务文件夹：

```text
WeChat-video-download/
  视频标题/
    video/视频标题.mp4
    transcript/视频标题.md
    audio/chunk_000.mp3
```

- 用户说“下载视频”：只保留 `video/`。
- 用户说“提取逐字稿”：成功后只保留 `transcript/`，自动删除中间视频和音频。
- 明确要求视频和逐字稿：保留 `video/` 与 `transcript/`。
- 只有明确要求保留音频时才保留 `audio/`。
- 转写失败时会保留中间文件，便于续跑和排查。

可以通过环境变量修改输出位置：

```dotenv
WECHAT_OUTPUT_DIR=D:\Downloads\WeChat-video-download
```

## 长视频和逐字稿处理

- 音频默认切成连续的 5 分钟片段，片段之间不留空隙。
- 默认并发识别 4 个片段。
- 每个完成的片段会缓存结果，失败重跑时可以继续。
- 默认输出 Markdown 逐字稿，不自动生成摘要。

可调整：

```dotenv
ASR_SEGMENT_SECONDS=300
ASR_WORKERS=4
```

## 这个 Skill 解决过哪些常见问题

- 防止 38 KB 封面图被误报成视频。
- 防止下载第二个链接时复用第一个视频缓存。
- Playwright 缺失时明确报告环境错误，不误判成直播回放。
- 直播页面预加载多个作品时，避免下载到其他视频。
- 转写任务默认清理中间视频和音频，只保留用户要求的结果。
- 兼容 Hermes、Claude Code 与 Codex 不同的 Python 运行环境。
- 通过 UTF-8 启动参数减少 Windows 中文乱码。

详细排查见 [references/troubleshooting.md](references/troubleshooting.md)。

## 安全与隐私

- 仓库不包含 API Key、Cookie、元宝登录配置、微信日志、媒体签名 URL 或解密参数。
- `.env` 已加入 `.gitignore`，不要把自己的 API Key 提交到 GitHub。
- 元宝登录状态和直播回放临时状态只保存在本机。
- 只处理你有权查看、保存和转写的内容，并遵守平台条款和当地法律。

## 更新

```powershell
Set-Location "$HOME\.agents\skills\wechat-video-subtitle"
git pull
python -m pip install -r scripts\requirements.txt
```

## License

MIT。ISAAC64 的 Python 适配来源与许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
