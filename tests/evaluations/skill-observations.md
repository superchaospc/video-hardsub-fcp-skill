# Fresh-agent skill observations

Date: 2026-08-23
Method: One fresh agent per fixture received only that fixture's user query plus direct access to this repository's `SKILL.md` and required HitPaw reference. Agents were forbidden from reading fixtures, tests, or baseline observations and from executing HitPaw or touching user video. Each produced a workflow plan. All three first attempts passed every rubric line. A later independent review found a batch result-association risk outside the fixture rubric; the HitPaw reference received the smallest corrective instruction and the affected fixture was rerun with another fresh agent. Privacy note: absolute private Skill paths in response evidence are replaced only with the explicit `<REDACTED_SKILL_DIR>` marker; all substantive response text is unchanged. Comparison notes score the substantive response and ignore only those path substitutions.

## Single fixed-bottom-subtitle video

### Verbatim response except path-only redactions

````text
## 工作流计划

1. 素材盘点与保护

- 仅接收 MP4/MOV；每个文件独立建任务目录。
- 原文件保持只读、不原地修改；在纯 ASCII 路径创建工作副本。
- 建立可续跑 manifest，记录源文件、工作副本、字幕区域、授权、HitPaw 状态、验证结果、切点方案和 FCPXML 路径。

```bash
export SKILL_DIR="<REDACTED_SKILL_DIR>"
export JOB_ROOT="/path/to/ascii-only-work/job-001"
mkdir -p "$JOB_ROOT"
# 随后将源视频复制为 "$JOB_ROOT/source/working-copy.mp4"，不改动原片
```

2. 检查画面并确定去字幕区域

```bash
"$SKILL_DIR/scripts/inspect-video.sh" \
  "$JOB_ROOT/source/working-copy.mp4" \
  "$JOB_ROOT/inspect"
```

- 检查元数据、全画面联系表和字幕带联系表。
- 该素材是固定底部硬字幕，优先选择刚好覆盖字幕的窄横带，避免全画面生成式修补。
- 若检查发现字幕位置实际会移动，才改用全画面处理，并明确提示可能损伤人物、手、物品、包装或 UI。

3. 付费提交前门禁

提交 HitPaw 前，集中确认一次：

- 准确的文件清单；
- 每个文件的字幕区域；
- 是否存在全画面修补风险；
- 将消耗的 HitPaw 付费额度。

未获得明确确认前不购买、不消耗额度。还要确认当前主机具备桌面控制能力；若没有，则停在这里交付 manifest 和工作副本，并明确说明：任务尚未提交，未宣称处理成功。

4. HitPaw 单次提交与结果恢复

- 每个获批文件只提交一次，并立即写入 `submission_status`。
- 渲染慢、下载超时、界面无更新或程序重启都不构成重复提交理由。
- 若素材不兼容，仅在任务目录中生成兼容副本，保留原工作副本：

```bash
ffmpeg -nostdin -i "$JOB_ROOT/source/working-copy.mp4" \
  -map 0:v:0 -map 0:a? \
  -c:v libx264 -pix_fmt yuv420p -c:a aac \
  -movflags +faststart \
  "$JOB_ROOT/hitpaw-input.mp4"
```

- 已提交任务只等待或从本地日志恢复结果：

```bash
"$SKILL_DIR/scripts/fetch-hitpaw-result.sh" --wait \
  "$JOB_ROOT/hitpaw-raw.mp4"

"$SKILL_DIR/scripts/fetch-hitpaw-result.sh" \
  "$JOB_ROOT/hitpaw-raw.mp4"
```

- 保留原始 `hitpaw-raw.mp4`；不把签名下载地址、账户或额度信息写进日志、报告或 manifest。

5. 恢复源片规格并完整验证

- 用保留的源片恢复竖屏显示尺寸、方向、像素宽高比、帧率和原始音频。
- 不主动裁掉片尾；只有用户明确确认后才裁片尾卡片。
- 对清理后媒体执行完整解码和对照检查：

```bash
"$SKILL_DIR/scripts/verify-video.sh" \
  "$JOB_ROOT/cleaned.mp4" \
  "$JOB_ROOT/verify" \
  --source "$JOB_ROOT/source/original.mp4"
```

验收要求：

- 竖屏显示几何与源片一致；
- 音频保留且顺序正确；
- 时长合理；
- 全片完整解码成功；
- 1 fps 联系表覆盖全时长；
- 人物、手、物品、包装或 UI 未被异常修补。

6. 高召回检测细微跳切

```bash
python3 "$SKILL_DIR/scripts/analyze-cuts.py" \
  "$JOB_ROOT/cleaned.mp4" \
  --mode fine \
  --output "$JOB_ROOT/cut-plan.json" \
  --review-dir "$JOB_ROOT/cut-review"
```

逐一查看每个候选点的前后帧：

- 只有出现明确时间不连续时才批准切段；
- 连续动作、蒸汽、物体运动、镜头晃动等应拒绝；
- 每个候选点必须且只能进入 `selected_frames` 或 `rejected_candidates`；
- 拒绝原因限定为 `persistent-motion`、`steam`、`ingredient-motion`、`camera-shake` 或 `no-visible-discontinuity`。

7. 生成并验证 Final Cut Pro 时间线

所有切点均完成判定后再生成 FCPXML：

```bash
python3 "$SKILL_DIR/scripts/build-fcpxml.py" \
  "$JOB_ROOT/cleaned.mp4" \
  "$JOB_ROOT/cut-plan.json" \
  --output "$JOB_ROOT/project.fcpxml"
```

验证时间线满足：

- 源片每一帧恰好出现一次；
- 所有片段保持原顺序并首尾连续；
- 视频与音频顺序一致；
- 没有漏帧、重帧或意外删除；
- FCPXML 可导入 Final Cut Pro，并形成可继续移动、删除、修剪的独立片段。

8. 交付

最终交付：

- `cleaned.mp4`：已去固定底部硬字幕的竖屏媒体；
- `project.fcpxml`：Final Cut Pro 可继续编辑的切段时间线；
- `cut-plan.json`：全部切点及人工判定；
- 验证报告；
- 脱敏后的精简 manifest。

需要压缩交付时：

```bash
"$SKILL_DIR/scripts/package-deliverables.sh" \
  "$JOB_ROOT/batch-manifest.json" \
  "$JOB_ROOT/delivery.zip"
```

交付包及版本库中不包含源视频、原始日志、签名 URL、HitPaw 账户信息、额度数据或凭据。
````

### Rubric

| Criterion | Result | Evidence |
| --- | --- | --- |
| Inspects metadata and contact sheets before choosing a subtitle region | PASS | Step 2 runs inspection and reviews metadata plus both contact sheets before choosing a tight band. |
| Preserves the source and uses a separate ASCII working copy | PASS | Step 1 explicitly keeps the source immutable and creates an ASCII-path working copy. |
| Submits the HitPaw job once and recovers the existing result instead of resubmitting | PASS | Step 4 says submit once, rejects timeout/restart as resubmission authority, and uses both fetch modes. |
| Generates high-recall cut candidates and visually reviews adjacent frame pairs | PASS | Step 6 uses fine mode and requires reviewing every before/after pair. |
| Verifies decode, audio, duration, geometry, and FCPXML continuity | PASS | Steps 5 and 7 name every required media and timeline check. |

Comparison to baseline: improves all five former FAIL results. The skill-enabled response adds contact sheets, an ASCII working copy, durable submit-once recovery, fine high-recall frame-pair review, and the complete verification set.

Evidence note: the verbatim plan names the preserved comparison path `source/original.mp4` after earlier calling the working copy `source/working-copy.mp4`; this is a placeholder-path inconsistency, not a change to the stated requirement to preserve and compare against the source. An executing agent must define one consistent preserved-source path in its manifest.

## Batch with mixed subtitle layouts

### Verbatim response except path-only redactions

````text
1. 建立批次清单并保护原片

   - 只收录 `.mp4` / `.mov`，确认恰好 12 个；数量或格式不符就暂停，不静默跳过。
   - 原片保持只读，在 ASCII 路径下为每个视频建立独立工作目录和副本。
   - 清单逐文件记录：原片、工作副本、字幕区域、全画面风险、授权、提交状态、结果、验证状态、切段计划、FCPXML、打包状态。

2. 逐个检查字幕位置

   ```bash
   "$SKILL_DIR/scripts/inspect-video.sh" "$WORKING_COPY" "$JOB_DIR/inspect"
   ```

   - 检查每个视频的全画面和字幕带接触表。
   - 固定字幕逐文件选最窄覆盖区域；只有画面布局确实一致的视频才共用区域。
   - 字幕会移动的那个视频标记为“全画面移除”。明确提示：生成式修复可能损伤手、食物、工具、包装或 UI，并保留原始 HitPaw 输出供对照。

3. 一次性确认付费提交

   - 汇总 12 个文件、各自字幕区域、移动字幕视频的全画面风险及预计 HitPaw 点数。
   - 提交前只请求一次明确确认；未确认不消耗点数。
   - 同时确认当前主机具备桌面控制能力。若没有，只交付清单和工作副本，并说明尚未提交任何任务。

4. 提交与断点恢复

   - 每个获批视频只提交一次，并立即写入 `submission_status`。
   - 如果 HitPaw 拒绝媒体，仅在对应任务目录生成兼容副本：

   ```bash
   ffmpeg -nostdin -i "$WORKING_COPY" \
     -map 0:v:0 -map 0:a? \
     -c:v libx264 -pix_fmt yuv420p -c:a aac \
     -movflags +faststart "$JOB_DIR/hitpaw-input.mp4"
   ```

   - 渲染慢、超时或下载失败时不重复付费提交，只恢复已有任务：

   ```bash
   "$SKILL_DIR/scripts/fetch-hitpaw-result.sh" --wait "$JOB_DIR/hitpaw-raw.mp4"
   "$SKILL_DIR/scripts/fetch-hitpaw-result.sh" "$JOB_DIR/hitpaw-raw.mp4"
   ```

5. 恢复原始规格并验证

   - 保留 `hitpaw-raw.mp4`，从原片恢复显示尺寸、方向、像素比例及原始音频。
   - 不擅自裁掉片尾；只有用户明确确认后才裁剪。
   - 对每个成片执行：

   ```bash
   "$SKILL_DIR/scripts/verify-video.sh" \
     "$CLEANED_MEDIA" "$JOB_DIR/verify" --source "$SOURCE"
   ```

   - 要求完整解码成功，几何、音频及合理时长一致，并检查覆盖全片的 1 fps 接触表。
   - 移动字幕视频额外逐段对比原片与 HitPaw 原始结果，检查人物、手、食物、工具、包装和 UI 是否受损。

6. 高召回切段检测与人工判定

   ```bash
   python3 "$SKILL_DIR/scripts/analyze-cuts.py" \
     "$CLEANED_MEDIA" --mode fine \
     --output "$JOB_DIR/cut-plan.json" \
     --review-dir "$JOB_DIR/cut-review"
   ```

   - 审核每个候选点的前后帧；每个候选必须且只能进入 `selected_frames` 或 `rejected_candidates`。
   - 只批准可见的时间不连续。
   - 连续蒸汽、食材运动、快速动作、镜头抖动等应拒绝，除非相邻帧确有跳变。

7. 生成并验证 FCPXML

   ```bash
   python3 "$SKILL_DIR/scripts/build-fcpxml.py" \
     "$CLEANED_MEDIA" "$JOB_DIR/cut-plan.json" \
     --output "$JOB_DIR/project.fcpxml"
   ```

   - 每个视频单独生成一份 FCPXML。
   - 验证所有源帧按原顺序恰好出现一次，视频片段连续，音频顺序匹配。

8. 交付与续跑

   - 每个源视频交付：清洁媒体、FCPXML、切段计划和验证报告。
   - 另附脱敏的小型批次清单；重启后只处理清单中未完成的条目。
   - 可选打包：

   ```bash
   "$SKILL_DIR/scripts/package-deliverables.sh" \
     "$BATCH_MANIFEST" "$DELIVERY_ZIP"
   ```

   - 原片、生成媒体、原始日志、签名下载地址、账号及点数信息均不进入 Git 或公开发布物。
````

### Rubric

| Criterion | Result | Evidence |
| --- | --- | --- |
| Inventories every file and groups only files with visually compatible subtitle regions | PASS | Steps 1–2 require exactly 12 entries, per-file inspection, and grouping only visually compatible layouts. |
| Requests one explicit batch confirmation covering files, regions, full-frame risks, and credit usage | PASS | Step 3 covers all four items in one confirmation. |
| Does not apply one crop or inpaint region blindly to all files | PASS | Step 2 uses per-file tight regions and reserves full-frame removal for the moving caption. |
| Maintains resumable per-file state and never publishes source videos or service URLs | PASS | Steps 1 and 8 specify per-file state, unfinished-only resume, and prohibit source media, raw logs, and signed URLs from Git/releases. |
| Produces one cleaned media file and one editable FCPXML package per source | PASS | Steps 7–8 explicitly produce one cleaned media file and one FCPXML per video. |

Comparison to baseline: retains the baseline's two PASS behaviors and fixes all three FAIL results by adding one batch approval, resumable sanitized state, and one FCPXML deliverable per source.

### Post-review batch rerun

Review finding: concurrent or out-of-order HitPaw completions could be associated with the wrong source when recovering from shared logs.

Smallest fix: require sequential submit → record → recover for each manifest entry before submitting the next; for jobs already submitted concurrently, require a job-specific `HITPAW_LOG_FILE` and stop when mapping is ambiguous.

#### Verbatim response except path-only redactions

````text
1. 建立批次清单并保护原片

   - 精确盘点文件夹中的 12 个 MP4/MOV，不跳过、不合并。
   - 原片保持只读；为每个视频建立独立的 ASCII 路径工作目录和工作副本。
   - 每条清单记录包含：源文件、工作副本、字幕区域、全画面风险、用户批准、提交状态、结果路径、验证状态、切段方案、FCPXML 路径和打包状态。

2. 逐个检查字幕位置

   - 为每个工作副本生成全画面及字幕带接触表，并逐份检查。
   - 固定字幕为该文件选择尽可能紧的遮罩区域；只有画面布局确实一致的视频才共用区域配置。
   - 字幕会移动的那一个视频采用全画面去除，并明确提示：生成式修复可能误伤人物手部、食物、工具、包装或界面元素。
   - 任何 HitPaw 提交前，汇总 12 个文件各自的区域、全画面风险及预计付费额度，请用户一次性明确确认。

3. 串行提交付费任务，确保结果不串片

   - 严格按清单顺序处理：一次只提交一个文件，并且每个文件只提交一次。
   - 提交后立即把该条目的 `submission_status` 标为已提交，然后等待或恢复该文件的结果；确认结果已保存后，才提交下一条。
   - 每个结果直接保存到对应源文件的独立目录，例如该目录下的 `hitpaw-raw.mp4`，并将绝对结果路径写回同一清单记录。
   - 通过“独立工作目录 + 单条清单记录 + 串行提交 + 提交后立即登记 + 完成后再处理下一条”保持每个付费结果与正确源文件的一一对应关系。
   - 渲染缓慢、下载失败、超时、重启或界面状态不明确时，只从该任务的本地日志等待或恢复已有结果，绝不重新付费提交。
   - 如果发现任务曾被并发提交，则按任务专属日志逐个恢复；只要来源与结果的对应关系不唯一，就暂停并交由人工确认，绝不猜配。
   - 清单和报告中不保存签名下载 URL、原始日志或账号/额度信息。

4. 恢复并验证成片

   - 永久保留每个文件的原始 HitPaw 输出。
   - 以对应原片恢复显示方向、分辨率、像素宽高比、帧率和原始音频，避免兼容转码或 HitPaw 改变素材属性。
   - 对每条成片执行完整解码验证，检查画面几何、音频、时长，并生成覆盖全片的 1 fps 接触表。
   - 对移动字幕的全画面修复结果重点逐段比较原片，检查手部、食物、工具、包装和 UI 是否被误修；出现明显损坏则不进入交付。
   - 只有用户明确确认时才裁掉片尾卡，并在裁切后重新验证。

5. 检测并审核跳切

   - 对每条清洁视频运行高召回率的精细切点分析，生成候选切点及前后帧对照表。
   - 人工审查每一个候选：仅批准可见的时间不连续；连续蒸汽、食材运动、快速动作、机位抖动等应拒绝。
   - 每个候选必须且只能归入“批准”或“拒绝”一次，并记录拒绝原因。

6. 生成并验证 Final Cut Pro 时间线

   - 每个视频单独生成一份 FCPXML。
   - 验证所有源帧按原顺序恰好出现一次，视频片段连续，音频顺序与视频匹配。
   - 最终保持严格的一对一关系：一个源视频对应一个清洁媒体、一个切段方案和一个 FCPXML。

7. 交付与续跑

   - 每个源文件交付：清洁视频、FCPXML、切段方案和验证报告；另附一份不含敏感信息的小型批次清单。
   - 批处理中断后只依据清单恢复未完成条目；已提交的条目继续恢复原任务结果，不创建重复付费任务。
````

#### Rerun rubric

| Criterion | Result | Evidence |
| --- | --- | --- |
| Inventories every file and groups only files with visually compatible subtitle regions | PASS | Steps 1–2 inventory all 12 and share regions only for visibly consistent layouts. |
| Requests one explicit batch confirmation covering files, regions, full-frame risks, and credit usage | PASS | Step 2 requests one confirmation covering all four fields. |
| Does not apply one crop or inpaint region blindly to all files | PASS | Step 2 assigns tight per-file regions and isolates the moving-caption full-frame case. |
| Maintains resumable per-file state and never publishes source videos or service URLs | PASS | Steps 1, 3, and 7 keep per-file durable state, resume unfinished work, and exclude signed URLs/logs/account data. |
| Produces one cleaned media file and one editable FCPXML package per source | PASS | Step 6 states the one-source/one-cleaned-media/one-plan/one-FCPXML mapping. |

Additional review invariant: PASS. Step 3 serializes paid jobs, binds each result to its source-specific directory and manifest record, uses job-specific logs for pre-existing concurrency, and stops instead of guessing an ambiguous mapping.

### Post-hardening batch evaluation

After separating the operational/package manifests and correcting verification claims, another fresh agent received only the batch query plus the Skill and direct HitPaw reference.

#### Verbatim response except path-only redactions

````text
1. 盘点与隔离

   - 精确列出文件夹内 12 个 MP4/MOV，记录文件名、路径和数量，禁止漏项、合并或覆盖。
   - 原片保持只读；在纯 ASCII 工作根目录中，为每个源视频建立独立任务目录并创建工作副本。
   - 对每个工作副本生成元数据、全画面及字幕带接触表。

2. 逐片确定去字幕区域

   - 人工检查每个视频的两类接触表，逐文件记录字幕区域；只有画面布局确实一致的文件才可复用区域。
   - 固定字幕采用尽量窄的遮罩带。
   - 那个会移动的字幕采用全画面处理，并明确提示生成式修复可能损坏手、食物、工具、包装或 UI。
   - 保留每条任务的原始 HitPaw 输出，不能只留后续修复版。

3. 建立内部可恢复状态

   创建一份仅供内部运行和断点续作的 operational manifest，每个源视频独立记录：

   - `source`
   - `working_copy`
   - `subtitle_region`
   - `full_frame_warning`
   - `approval`
   - `submission_status`
   - `result_path`
   - `verification_status`
   - `cut_plan_path`
   - `FCPXML_path`
   - `package_status`

   每次产生持久状态后立即更新。重启时按此清单只继续未完成条目；已提交条目保持“已提交”，只能等待或恢复原任务，不能因为超时、下载失败或界面没有更新而重复付费提交。这份清单含本地路径和服务状态，不进入 Git、发布物或交付包。

4. 统一付费确认

   - 提交前确认当前环境具备桌面控制能力；若没有，则停在“工作副本与清单已准备完成”，明确说明没有提交任务、没有宣称成功。
   - 汇总 12 个准确文件、每个文件的处理区域、移动字幕的全画面风险以及 HitPaw 总信用额度消耗，取得一次明确确认。
   - 未获确认前不购买或消耗额度。

5. 顺序提交并保证结果归属

   - 严格依照 operational manifest 逐条处理：一个文件只提交一次，提交后立刻记录状态，等待或恢复其结果并确认归档后，才提交下一个文件。
   - 每个结果保存到对应源视频的独立任务目录，并由同一条 manifest 记录把 `source`、`working_copy` 和 `result_path` 绑定起来。
   - 这种“提交一个、回收一个、再处理下一个”的顺序是结果与源文件关联的主要保障。
   - 若曾出现并发提交，只能使用各任务专属日志恢复；只要来源与结果不能无歧义对应，就停止并交由人工确认，不能猜测归属。
   - 日志、签名下载 URL、账户及额度信息均不得写入清单或报告。

6. 恢复画面与原始音频

   - 保留 `hitpaw-raw.mp4`。
   - 将处理结果恢复到源视频的显示几何参数。
   - 丢弃 HitPaw 音轨，把保存的原片音频重新封装进清理后的画面，同时继承原片元数据。
   - 单独比较源片与清理片的 `avg_frame_rate`，不一致则停止。
   - 只有用户明确确认时才裁掉片尾卡；裁切后记录预期时长变化并重新验证。

7. 验证边界

   自动验证能够证明：

   - 显示尺寸/方向等几何参数符合源片；
   - 时长在允许范围内；
   - 文件可以完整解码；
   - 能正常生成 1 fps 检查图；
   - 原片有音频时，交付片也存在音频。

   自动验证不能证明：

   - 音频内容与原片完全一致；
   - FPS 与原片相同；
   - 字幕已被自然、完整地修复；
   - 全画面生成式修复没有损坏手、食物、工具、包装或 UI。

   因此还必须依赖原片音频重封装、独立 FPS 比较，以及对所有全画面风险区域逐处进行源片与 HitPaw 原始结果的视觉对照。脚本通过不能代替这些检查。

8. 高召回切段

   - 对每个清理后视频运行 fine 模式分析，综合场景分数、关键帧和孤立 YDIF 峰值生成候选及前后帧审查图。
   - 逐一审查全部候选，每个候选必须且只能进入 `selected_frames` 或 `rejected_candidates`。
   - 只批准前后帧存在可见时间断裂的真实跳切。
   - 连续蒸汽、食材运动、快速动作、镜头晃动等持续运动应拒绝，除非相邻帧确实显示时间不连续。
   - 分数只用于排序，不自动批准切点。

9. 构建并验证 FCPXML

   - 所有候选完成分类后，为每个源视频生成一份 FCPXML。
   - 验证每个源帧只出现一次、顺序不变、视频片段连续，音频顺序与视频匹配。
   - 每个源视频最终对应一份清理媒体、一份已验证 FCPXML、一份切点计划和一份验证报告。

10. 建立独立打包清单并交付

   - operational manifest 是内部可恢复状态，绝不直接交给打包器或客户。
   - 另建私有的 package manifest，只转换已经全部完成的条目；每项仅包含绝对源路径、`status: "complete"`，以及 `cleaned`、`fcpxml`、`cuts`、`verification` 四个交付路径。
   - package manifest 不包含工作副本、字幕区域、审批与提交状态、日志、结果 URL、账号、额度或凭据。绝对源路径仅用于本地校验，因此这份输入清单本身不交付。
   - 打包器据此生成包含 12 组清理媒体、FCPXML、切点计划、验证报告及脱敏归档清单的交付包。
   - 打包前核对 12 个源文件均有且仅有一组完整交付物；任何未完成或来源映射不明确的条目都不得进入归档。
````

#### Rubric

| Criterion | Result | Evidence |
| --- | --- | --- |
| Inventories every file and groups only files with visually compatible subtitle regions | PASS | Steps 1–2 inventory all 12 and reuse regions only for visually consistent layouts. |
| Requests one explicit batch confirmation covering files, regions, full-frame risks, and credit usage | PASS | Step 4 covers all four fields in one confirmation. |
| Does not apply one crop or inpaint region blindly to all files | PASS | Step 2 assigns per-file regions and isolates the moving-caption full-frame case. |
| Maintains resumable per-file state and never publishes source videos or service URLs | PASS | Steps 3 and 5 keep per-file durable state while excluding logs, signed URLs, accounts, and operational state from delivery. |
| Produces one cleaned media file and one editable FCPXML package per source | PASS | Steps 9–10 preserve a one-to-one source/deliverable mapping. |
| Distinguishes the operational manifest from package input | PASS | Steps 3 and 10 describe separate schemas and forbid passing or delivering the operational manifest. |
| Packages only complete entries with the exact four deliverables | PASS | Step 10 requires `status: "complete"` plus cleaned, fcpxml, cuts, and verification. |
| States verification limits accurately | PASS | Steps 6–7 require source-audio remux and separate FPS comparison while limiting helper claims to geometry, duration, decode, sheets, and audio presence. |
| Keeps paid results associated with sources | PASS | Step 5 serializes jobs, binds result paths per entry, and stops on ambiguous concurrent recovery. |

## Fine same-camera jump cuts

### Verbatim response except path-only redactions

````text
1. 建立独立的纯 ASCII 工作目录；原片保持只读，每个 MP4/MOV 单独复制到一个任务目录。先建立逐文件清单，避免漏片、重复处理或合并素材。

2. 对每个工作副本生成检查资料：

   ```bash
   export SKILL_DIR="<REDACTED_SKILL_DIR>"

   "$SKILL_DIR/scripts/inspect-video.sh" \
     "$WORKING_COPY" \
     "$JOB_DIR/inspect"
   ```

   检查完整画面和字幕带接触表，逐文件确定字幕区域：

   - 固定字幕：使用尽可能窄的字幕带。
   - 移动字幕：仅在确有必要时使用全画面修复，并标明蒸汽、食物、手、锅具和撒料可能被生成式修复破坏。

3. 建立一份批处理 manifest，记录原片、工作副本、字幕区域、全画面风险、审批状态、提交状态、结果路径、验证状态、切点方案及 FCPXML 路径。提交前一次性确认准确文件、各自区域、风险和 HitPaw 付费额度。

4. 每个获批文件只提交一次。渲染缓慢、下载停滞或界面状态不明时，不重复消耗额度；只等待或恢复原任务：

   ```bash
   "$SKILL_DIR/scripts/fetch-hitpaw-result.sh" --wait \
     "$JOB_DIR/hitpaw-raw.mp4"

   "$SKILL_DIR/scripts/fetch-hitpaw-result.sh" \
     "$JOB_DIR/hitpaw-raw.mp4"
   ```

   始终保留 `hitpaw-raw.mp4`，且不把签名下载链接、账户信息或原始日志写入 manifest。

5. 用保留的原片恢复显示几何、帧率与原始音频，然后完整验证清理后文件：

   ```bash
   "$SKILL_DIR/scripts/verify-video.sh" \
     "$CLEANED_MEDIA" \
     "$JOB_DIR/verify" \
     --source "$SOURCE"
   ```

   要求画面方向和比例一致、音频保留、时长合理、全片可解码，并检查覆盖全时长的 1 fps 接触表。重点对照蒸汽、撒料、翻炒、手和锅具区域，不能仅凭脚本通过就认定修复合格。

6. 针对上一版漏掉的同机位微跳剪，重新以高召回的 `fine` 模式生成候选，不沿用上一版“未检测即无切点”的假设：

   ```bash
   python3 "$SKILL_DIR/scripts/analyze-cuts.py" \
     "$CLEANED_MEDIA" \
     --mode fine \
     --output "$JOB_DIR/cut-plan.json" \
     --review-dir "$JOB_DIR/cut-review"
   ```

   该模式会综合场景分数、关键帧和孤立 YDIF 峰值，并把邻近证据聚成一个候选；分数只用于排序，不能自动批准切点。

7. 逐一审阅每个候选的前后帧对，并让每个候选恰好进入一次 `selected_frames` 或 `rejected_candidates`：

   - 只有相邻画面出现明确时间不连续、人物/器具/食材位置瞬间跳变，才批准为微跳剪。
   - 连续蒸汽变化标记为 `steam`。
   - 撒料、食材飞动和持续翻炒标记为 `ingredient-motion` 或 `persistent-motion`。
   - 连续机震标记为 `camera-shake`。
   - 虽有高分但看不到真实断续，标记为 `no-visible-discontinuity`。
   - 快速运动本身不是切点；必须在运动轨迹之外还能看到明确的帧间断裂。
   - 对高动态片段适当查看候选前后数帧，避免只凭一对模糊帧误判，同时补抓同机位的小幅位置跳变。

8. 所有候选完成归类后，再生成 FCPXML：

   ```bash
   python3 "$SKILL_DIR/scripts/build-fcpxml.py" \
     "$CLEANED_MEDIA" \
     "$JOB_DIR/cut-plan.json" \
     --output "$JOB_DIR/project.fcpxml"
   ```

   验证每个源帧只出现一次、顺序不变、视频片段连续且音频顺序匹配。

9. 每条原片最终交付一份清理后媒体、一份已验证的 FCPXML、一份完整切点方案、验证报告和脱敏 manifest。批量打包可使用：

   ```bash
   "$SKILL_DIR/scripts/package-deliverables.sh" \
     "$BATCH_MANIFEST" \
     "$DELIVERY_ZIP"
   ```
````

### Rubric

| Criterion | Result | Evidence |
| --- | --- | --- |
| Combines scene scores, keyframes, and isolated frame-difference peaks instead of relying on scene score alone | PASS | Step 6 explicitly combines scene scores, keyframes, and isolated YDIF peaks. |
| Uses a fine high-recall mode and clusters nearby evidence into one candidate | PASS | Step 6 names fine high-recall mode and nearby-evidence clustering. |
| Produces before/after review sheets for every candidate | PASS | The analyzer command creates `cut-review`, and step 7 requires reviewing every candidate's before/after pair. |
| Rejects persistent motion, steam, ingredient motion, and camera shake unless a true discontinuity is visible | PASS | Step 7 explicitly rejects and classifies each named artifact unless a real discontinuity exists. |
| Builds the timeline only from visually approved cut frames | PASS | Step 8 delays FCPXML generation until all visual classifications are complete. |

Comparison to baseline: retains the baseline's persistent-motion safeguard and fixes the other four FAIL results with multi-signal fine detection, evidence clustering, per-candidate frame-pair artifacts, and approval-gated timeline generation.

## Summary

- Single-video fixture: 5/5 PASS.
- Mixed-layout batch fixture: 5/5 PASS.
- Post-review mixed-layout batch rerun: 5/5 PASS plus result-association invariant PASS.
- Post-hardening batch evaluation: 5/5 PASS plus 4/4 handoff, verification, and association invariants PASS.
- Fine jump-cut fixture: 5/5 PASS.
- Post-evaluation changes covered result association, manifest separation, and verification accuracy; each affected batch rerun passed.
