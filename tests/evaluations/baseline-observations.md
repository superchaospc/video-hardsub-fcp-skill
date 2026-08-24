# Fresh-agent baseline observations

Date: 2026-08-21
Method: Three fresh agents received only the corresponding query and were explicitly barred from reading local video skills, repository files, or scripts. Each returned a workflow plan; the full responses are retained below. Scores reflect only what each response states.

## Single fixed-bottom-subtitle video

### Baseline response

> 计划如下（不读取或修改任何本地文件）：
>
> 假设素材是单条竖屏 MP4，底部字幕为烧录在画面里的固定位置字幕；目标是在 Final Cut Pro 中继续精剪，而不是只交付一个重新压缩的成片。
>
> 1. 先检查素材规格：时长、帧率、分辨率、编码、旋转信息、音频轨道，以及字幕区域是否始终固定、是否遮挡主体或重要画面。
> 2. 按字幕变化点切段。每句/每次字幕出现、消失或换行都会成为一个时间线切点；这样后续修复只影响必要片段，也便于在 FCP 内单独替换或调整。
> 3. 对每段底部字幕区域做去除修复：优先使用周边背景重建/修补，保持原始竖屏构图与帧率；如字幕压住人物、手部或移动物体，标记为高风险段，采用逐段遮罩、跟踪或保留备用版本，而不以粗暴裁切掩盖；不改变原始音频，不擅自加字幕、滤镜或调色。
> 4. 导出可继续编辑的交付包：修复后的逐段媒体文件（建议使用便于 FCP 编辑的 ProRes 422；必要时另附较小的审阅版 H.264）；一个 FCPXML 时间线，将所有片段按原始顺序、原始时间码和无缝切点排列；原始 MP4 保持不动，并作为可回退素材保留；一份修复备注。
> 5. 验收时逐段对照原片，重点检查字幕边缘、人物经过底部区域时的形变、镜头运动中的背景稳定性、音画同步和总时长一致性。时间线会保留段落边界，方便在 Final Cut Pro 里继续剪辑、替换或微调。

| Rubric | Score | Evidence |
| --- | --- | --- |
| Inspects metadata and contact sheets before choosing a subtitle region | FAIL | Metadata is inspected; contact sheets are not mentioned. |
| Preserves the source and uses a separate ASCII working copy | FAIL | Source preservation is stated; a separate ASCII working copy is not. |
| Submits the HitPaw job once and recovers the existing result instead of resubmitting | FAIL | No job submission, polling, or recovery procedure is stated. |
| Generates high-recall cut candidates and visually reviews adjacent frame pairs | FAIL | Cuts are based on subtitle changes; high-recall candidates and adjacent-frame review are not stated. |
| Verifies decode, audio, duration, geometry, and FCPXML continuity | FAIL | Audio and total duration are checked, but decode, geometry, and FCPXML continuity checks are not stated. |

## Batch with mixed subtitle layouts

### Baseline response

> 可以。我会按“逐视频检测、分组处理、异常单独复核”的方式做，避免把不同字幕位置的视频套同一套参数。
>
> 1. 盘点素材：读取每条视频的时长、分辨率、帧率、音轨及编码信息；为 12 条视频建立编号和处理清单，原文件不改动。
> 2. 抽帧定位字幕：每条视频按开头、中段、结尾及场景变化处抽样，识别字幕的位置、大小、颜色和背景特征。固定位置相近的视频归入同一组；位置明显不同的视频单独处理。
> 3. 为固定字幕视频建立遮罩：对每一组生成对应的字幕区域遮罩；每条视频保留独立参数，防止误伤画面。
> 4. 对移动字幕视频做逐段跟踪：先检测字幕移动轨迹和场景切换，再按短片段更新遮罩位置；必要时按镜头切分处理，而不是用一个固定框覆盖全片。
> 5. 去字幕后质量检查：每条视频至少检查开头、中段、结尾及字幕移动/场景切换位置，排查字幕残留、修补伪影、关键元素误擦和音画同步、帧率、时长异常。
> 6. 切段输出：根据确认的切段规则输出段落，保持原始音频同步。
> 7. 交付与留档：交付去字幕后的切段视频、处理清单和异常报告。
>
> 安全措施：原始 12 个视频保持只读；输出写入独立文件夹；自动处理前先以每种字幕位置做小范围样片验证；对移动字幕、遮挡主体或修补质量低的段落不强行“修干净”。

| Rubric | Score | Evidence |
| --- | --- | --- |
| Inventories every file and groups only files with visually compatible subtitle regions | PASS | It inventories all 12 files, samples layouts, and groups only similar fixed positions. |
| Requests one explicit batch confirmation covering files, regions, full-frame risks, and credit usage | FAIL | It proposes sample validation but no explicit batch confirmation or credit discussion. |
| Does not apply one crop or inpaint region blindly to all files | PASS | It uses per-group masks, per-file parameters, and tracked segments for moving subtitles. |
| Maintains resumable per-file state and never publishes source videos or service URLs | FAIL | It has a processing list but does not specify resumable state or handling of service URLs. |
| Produces one cleaned media file and one editable FCPXML package per source | FAIL | It promises cleaned cut videos, not an FCPXML package per source. |

## Fine same-camera jump cuts

### Baseline response

> 工作目标：在去除字幕的基础上，按真实镜头切点切段，并补回上一版遗漏的“同机位微跳剪”；蒸汽、撒料、快速翻炒等连续烹饪动作不作为切点。
>
> 1. 先定位字幕区域与形式；独立轨则移除，烧录字幕则采用局部遮罩、修补或画面裁切方案，并逐段检查不会误伤锅具、食材和手部。
> 2. 建立“候选切点”而非直接自动切段：强候选是食材/锅内状态/手或工具/火候突变；亮度、烟雾、抖动、快速运动造成的差异是弱候选，人工复核后决定。
> 3. 对构图相同的连续段，以较小时间粒度逐帧或慢放比对前后状态，并检查音频断裂或动作声被截断。只有视觉或音频至少一项支持时间跳跃时，才确认为切点。
> 4. 蒸汽、油烟、火焰、撒料、倒液体、快速翻炒、轻微抖动、运动模糊和曝光/焦点变化默认视为连续，除非同时出现明确状态跳变。
> 5. 每个确认点放在自然动作边缘；输出前逐段连播、慢放抽检，并交付分段视频、切点清单和未切连续运动备注。

| Rubric | Score | Evidence |
| --- | --- | --- |
| Combines scene scores, keyframes, and isolated frame-difference peaks instead of relying on scene score alone | FAIL | No scene-score, keyframe, or isolated frame-difference method is specified. |
| Uses a fine high-recall mode and clusters nearby evidence into one candidate | FAIL | Smaller-granularity review is stated, but no high-recall mode or evidence clustering is stated. |
| Produces before/after review sheets for every candidate | FAIL | It proposes review, but no review-sheet artifact for every candidate. |
| Rejects persistent motion, steam, ingredient motion, and camera shake unless a true discontinuity is visible | PASS | It explicitly treats those phenomena as continuous unless a state jump is present. |
| Builds the timeline only from visually approved cut frames | FAIL | It promises segmented video and a cut list, not a timeline constrained to visually approved frames. |

## Recurring gaps to address

- **Scene-score-only under-detection:** The fine-cut baseline did not propose scene scores, keyframes, or isolated frame-difference peaks. It therefore supplies no multi-signal mechanism to prevent scene-score-only under-detection of same-camera micro-jumps.
- **Unsafe blind batching:** This batch response correctly avoided a blind shared region (PASS), but it omitted the explicit batch confirmation that records selected files, regions, full-frame risks, and credit usage. The evaluation must continue guarding against unsafe blind batching despite this response's per-file safeguards.
- **Accidental resubmission after a slow HitPaw job:** No baseline response specified durable job state, polling, or recovery of an existing result. The single-video plan consequently fails the submit-once/recover-existing-result rubric and leaves accidental resubmission unaddressed.

Across all three responses, none described separate ASCII working copies, resilient external-job recovery, or the full decode/audio/duration/geometry/FCPXML continuity verification set.
