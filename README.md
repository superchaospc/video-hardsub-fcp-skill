# Video Hardsub → FCP Skill

[![CI](https://github.com/superchaospc/video-hardsub-fcp-skill/actions/workflows/validate.yml/badge.svg)](https://github.com/superchaospc/video-hardsub-fcp-skill/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/superchaospc/video-hardsub-fcp-skill)](https://github.com/superchaospc/video-hardsub-fcp-skill/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![macOS](https://img.shields.io/badge/platform-macOS-lightgrey)
![Codex](https://img.shields.io/badge/Codex-compatible-000000)
![Claude Code](https://img.shields.io/badge/Claude%20Code-compatible-D97757)
![FCPXML](https://img.shields.io/badge/Final%20Cut%20Pro-FCPXML-blue)

把视频发给代理并说：**去字幕切段**。

这是一个同时兼容 Codex 和 Claude Code 的脚本辅助型 Agent Skill。它组织硬字幕去除、精细跳剪复核、结果验证和 Final Cut Pro FCPXML 交付；源视频不会被原地修改。

## 功能

- 处理一个 MP4/MOV 文件，或逐个处理一个文件夹中的多个视频。
- 先生成全片接触表，再为每个视频选择尽可能小的字幕区域。
- 通过 HitPaw Edimakor 去除烧录在画面里的硬字幕，并从本地日志安全恢复已经完成的任务，避免重复消耗付费次数。
- 以高召回模式检测明显镜头切换和同机位微跳剪，为每个候选点生成前后帧复核图。
- 交付媒体和 FCPXML 工程统一为竖屏 1080×1920，导入 Final Cut Pro 即是竖屏时间线，不需要再手动改工程设置。
- 保留清理后视频和音频的完整顺序，在 FCPXML 时间线中建立可继续编辑的切段。
- 完整解码验证媒体，检查画面几何、交付尺寸、音频存在性、时长、FCPXML 连续性和交付包校验和。

## 前置条件

- macOS。
- Git、Python 3.10 或更高版本、`ffmpeg` 和 `ffprobe`。
- 已安装 HitPaw Edimakor，且当前代理宿主具备桌面控制能力，才能自动执行去字幕界面操作。
- 如需继续剪辑，已安装 Final Cut Pro。

HitPaw Edimakor 和 Final Cut Pro 都是需要用户自行安装、授权或购买的专有软件，本仓库不包含它们，也不会静默购买或消耗 HitPaw 点数。提交任何付费任务前，Skill 会要求一次明确确认。

## 安装

```bash
git clone https://github.com/superchaospc/video-hardsub-fcp-skill.git
cd video-hardsub-fcp-skill
bash scripts/install.sh --source "$PWD"
```

安装器将当前检出目录作为唯一来源，并安全建立以下发现路径：

- Claude Code：`$HOME/.claude/skills/video-hardsub-fcp`
- Codex：`$HOME/.codex/skills/video-hardsub-fcp`
- Codex 通用代理目录：`$HOME/.agents/skills/video-hardsub-fcp`

后两项指向 Claude Code 的规范路径。安装器不会替换无关的真实目录或错误链接；并发安装、进程中断或目录拓扑发生变化时会失败并保留可检查的恢复项。确认恢复项归属和内容前不要手动删除。

## 单文件使用

把视频拖入 Codex 或 Claude Code 对话，然后说：

```text
去字幕切段
```

代理会检查视频、展示字幕区域和全画面修复风险，并在使用 HitPaw 点数前请求确认。完成后，它会复核所有切点，生成清理后的媒体和 FCPXML。双击 `.fcpxml` 文件，或在 Final Cut Pro 中选择“文件 → 导入 → XML”，即可继续编辑。

## 批量使用

把多个视频或一个文件夹交给代理，然后说：

```text
这些视频全部去字幕切段，逐个处理，完成后打包。
```

批量流程会先列出准确文件清单、每个文件的字幕区域、全画面修复风险和预计提交次数，再请求一次总确认。每个条目保存独立状态；中断后只恢复未完成条目，不会把多个来源合并，也不会因等待或下载失败而重复提交付费任务。

## 输出与完整性

每个源视频对应一组结果：

- `cleaned.mp4` 或 `cleaned.mov`：去字幕、保留源音频的竖屏 1080×1920 媒体（见下文“竖屏 1080×1920 交付”）。
- `project.fcpxml`：可直接导入 Final Cut Pro 的连续切段时间线，工程格式固定为 1080×1920。
- `cut-plan.json`：所有候选切点、批准项与拒绝原因。
- `verify-delivery/report.json`：交付媒体的验证摘要（ZIP 内重命名为 `verification.json`）。转换前对恢复媒体的校验保存在 `verify/report.json`，留在工作目录中。
- 可选 ZIP：只包含完成条目、去标识化清单和 `SHA256SUMS`；每个 `entry-NNN` 内的归档专用 FCPXML 使用同目录相对媒体引用，不保留本机工作路径。

验证交付包：

```bash
unzip -t deliverables.zip
mkdir verified-delivery
unzip deliverables.zip -d verified-delivery
(cd verified-delivery && shasum -a 256 -c SHA256SUMS)
```

请先完整解压 ZIP，再从对应的 `entry-NNN` 目录导入 `project.fcpxml`；不要把 XML 与同目录的 `cleaned.mp4`/`cleaned.mov` 分开移动。归档后的切段计划保留候选帧、批准项和拒绝原因，但会移除本机工作目录、复核图路径等非交付字段。

FCPXML 中的切段是同一清理媒体上的可编辑边界，不会把视频强制导出成许多独立碎片；所有源帧按原顺序恰好出现一次。

## 竖屏 1080×1920 交付

无论原片是 720×1280、608×1080 还是横屏 1920×1080，交付的 `cleaned` 媒体和 FCPXML 工程都是 1080×1920。

- `scripts/conform-vertical.sh` 在媒体流程最后一步完成转换：等比缩放到能放进 1080×1920 的最大尺寸，比例不是 9:16 时居中并补黑边。不裁切、不拉伸，不丢帧也不改帧率，音频直接复制。
- 已经是 1080×1920 方形像素且无旋转的素材只做流复制，不重新压缩；其他尺寸以 x264 CRF 16 重新编码。
- 转换后校验帧数与原来一致，并运行 `verify-video.sh --delivery-size 1080x1920` 确认交付尺寸，同时仍对照原片检查时长、音频和完整解码。
- FCPXML 工程格式固定为 1080×1920；素材资源保留自身真实尺寸的格式，让 Final Cut Pro 正确适配画面。

放大不会补回细节，所以顺序是固定的：先把去字幕后的恢复媒体按**原片分辨率**校验，再转成 1080×1920。如果 HitPaw 或其他去字幕工具把 720×1280 悄悄降成了 608×1080，这一步会失败并写入报告；之后的放大只是交付格式，不会被当成修复，也不会掩盖这次降分辨率。

## 为什么还要精细复核

分析器组合场景分数、关键帧和孤立的逐帧差异峰值，因此能比单一场景阈值找到更多同机位微跳剪。分数只用于排序，不会自动批准切点。代理必须查看每个候选点，并把它明确归入批准或拒绝；蒸汽、落料、快速动作和抖动等连续运动通常应拒绝。高召回意味着候选可能偏多，但能减少漏掉细小跳剪的概率。

每个候选点落在证据最强的那一帧上，也就是新镜头的第一帧，这样 FCPXML 的切口不会混进上一镜头的一帧。翻炒、倒酱这类连续动作会产生大量弱场景分，把几十帧串成一组；分析器会在组内找出彼此相隔较远的强信号（关键帧、差异峰值或较高的场景分），各自拆成独立候选，避免同一段连续动作里的第二个真跳剪被吞掉。

复核图每个候选占一行四帧（候选帧的 -2、-1、0、+1）。只看候选帧和它的前一帧不足以判断：翻炒、蒸汽和手部动作的相邻帧差异同样很大，跟真跳剪难以区分。四帧连排后，运动要么在整行里连续延续，要么在切点处断开，判断依据直接可见。候选靠近首尾时窗口会夹取到边界并重复端帧，以保持每行列数固定。

复核图分页会按源片宽高比调整：竖屏每页 3 个候选，横屏 5 个。固定的 4×5 网格是按 16:9 设计的，用在 9:16 素材上会生成 1290×2862 的长图，缩放到阅读尺寸后每帧过小，细节不可辨。

## 安全与隐私

- 原片保持不变；工作副本、HitPaw 原始结果和最终媒体分开保存。
- 固定字幕优先使用窄区域。移动字幕需要全画面修复，可能损伤手、食物、工具、包装或界面，必须先警告并视觉对比。
- 已提交的 HitPaw 任务只恢复结果，不因超时、慢渲染或界面未刷新而重新提交。
- 不把原视频、生成媒体、原始日志、签名下载链接、账号/点数信息、凭据或本地工作清单提交到 Git 或 GitHub Release。
- 发布前运行仓库校验器，阻止常见媒体、日志、密钥、签名 URL 和私人主目录路径进入版本库。

## 局限

- 交付尺寸固定为 1080×1920。横屏或非 9:16 素材会带黑边，不会自动裁切铺满；原片低于 1080×1920 时，转换只是放大，不增加细节。
- 只支持 macOS 上的 MP4/MOV 工作流；桌面自动化取决于当前宿主的可用能力和 HitPaw 界面状态。
- HitPaw 每个任务只能框一个区域，导入片段至少 2 秒。字幕分在两处且相距较远时（例如全片底部字幕加开头几帧标题），要把第二处截成单独片段另外提交，再按帧号合成回去。
- 去字幕质量取决于 HitPaw 的生成式修复。复杂背景、移动字幕或全画面模式必须人工目检。
- 跳剪检测以减少漏检为目标，但视觉复核仍是必需步骤；它不能保证理解所有创作意图。
- 候选帧锚定的是一簇邻近证据。当这簇证据不含关键帧时，锚点帧可能落在可见切点之后一帧（误差 1/30 秒左右）。`build-fcpxml.py` 要求批准项必须取自候选列表，因此该偏差会保留到成片，可在 Final Cut Pro 中微调。
- FCPXML 提供切段时间线，不替代 Final Cut Pro，也不自动完成调色、配乐或成片导出。
- HitPaw 点数、软件许可证和网络服务费用不包含在本项目中。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v
bash tests/integration/synthetic-cuts.sh
bash -n scripts/*.sh
python3 scripts/validate-skill.py .
```

合成集成测试会在临时目录生成三个短镜头，验证按原片校验恢复媒体、转换为 1080×1920 并校验交付尺寸、精细切点检测、完整人工分类结构、FCPXML 时长、Apple FCPXML 1.10 DTD（本机可用时）、工作目录移走后的归档媒体解析和 ZIP 校验和；运行结束后会自动清理测试媒体。

## 许可证

代码和文档采用 [MIT License](LICENSE)。HitPaw Edimakor、Final Cut Pro 及其商标归各自权利人所有。

## English summary

Video Hardsub → FCP is a macOS Agent Skill shared by Codex and Claude Code. Give an agent one or more hard-subtitled MP4/MOV files and say `去字幕切段`; it coordinates explicit paid-job approval, safe HitPaw result recovery, high-recall jump-cut review, media verification, and an editable Final Cut Pro FCPXML timeline. Every delivery is vertical 1080x1920: the cleaned media is fit-scaled onto that frame (black padding when the aspect is not 9:16, never cropped or stretched, every frame kept) and the FCPXML project uses a 1080x1920 format. The restored media is verified against the source resolution before that conform, so an upstream downscale is still reported rather than hidden by the upscale. Each candidate lands on the first frame of the new shot, and a long stretch of continuous motion is split wherever it holds more than one strong boundary, so a second jump cut inside it is not lost. Review sheets show each candidate as four consecutive frames so continuous motion can be told apart from a real cut, and pages adapt to the source aspect ratio. HitPaw takes one selection box per job (clips of at least 2 s), so a second, distant caption region is handled as a separate short-clip job. HitPaw Edimakor and Final Cut Pro are proprietary user-installed prerequisites and are not bundled.
