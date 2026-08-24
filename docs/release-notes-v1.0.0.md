# Video Hardsub FCP Skill v1.0.0

首个公开版本提供一套可由 Codex 与 Claude Code 共用的 macOS 视频工作流。把视频交给代理并说“去字幕切段”，即可开始硬字幕清理、精细切点复核和 Final Cut Pro 时间线交付。

## 主要内容

- 一个仓库、一个规范 Skill：安装器在 Claude Code 目录建立来源，并为 Codex 的两个发现目录创建安全链接。
- 付费任务边界：提交 HitPaw Edimakor 前显示文件、区域、全画面风险与点数使用，并要求一次明确确认。
- 安全恢复：渲染慢、下载停滞或界面状态不明时，从已有任务的本地日志恢复结果，不重复提交付费任务。
- 精细跳剪复核：组合场景、关键帧和逐帧差异证据，为明显镜头切换与同机位微跳剪生成前后帧复核图；每个候选点必须明确批准或拒绝。
- FCPXML 交付：为每个清理后视频生成连续、帧对齐且保持完整音视频顺序的 Final Cut Pro 时间线。
- 交付验证：完整解码、画面几何、时长、音频存在性、XML 连续性、隐私扫描和可选 ZIP 校验和。

HitPaw Edimakor 和 Final Cut Pro 是用户自行安装和授权的专有软件，本版本不包含这些应用或任何用户媒体。

## 安装

```bash
git clone https://github.com/superchaospc/video-hardsub-fcp-skill.git
cd video-hardsub-fcp-skill
bash scripts/install.sh --source "$PWD"
```

## 验证此版本

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/*.sh
python3 scripts/validate-skill.py .
```

发布资产中的 `SHA256SUMS` 可用 `shasum -a 256 -c SHA256SUMS` 校验。仓库和发布资产不应包含源视频、生成媒体、HitPaw 原始日志、签名结果链接、账号信息或凭据。
