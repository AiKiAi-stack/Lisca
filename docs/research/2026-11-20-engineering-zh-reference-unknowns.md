# 中文 fixed02：43 项 unknown 的离线核查

日期：2026-11-20，Asia/Shanghai。按 `research` 技能核查 2026-11-18 已保存的中文 fixed02，完成一次冻结小样本离线实验。

**结论：原评分仍为 16/59 可测、43 unknown。** 五项样本中，1/58 的文字不匹配来自数字写法，2/50 的有限像素边界被保守规则丢弃；1/58/59 的波形归属继续 unknown。样本中没有支持产品字幕缺失的确认结论。建议下一切片先修复像素边界的区间保留，原分与发布结论保持。[E1], [S1]

## 原验收项与调查边界

父任务 [#99](https://github.com/AiKiAi-stack/Lisca/issues/99)，本切片 [#105](https://github.com/AiKiAi-stack/Lisca/issues/105)。原中文参考 59 句，每语至少 50 句可测，P95 ≤ 0.5 秒、最大 ≤ 1 秒、覆盖 ≥ 95%。目前报告为 16/59 可测、43 项 unknown。遵循 [ADR-0011](../adr/0011-browser-clock-engineering-sync.md)，结论称浏览器输出时钟估计，物理声学误差与旧 #70 结论继续保留。

调查只读取已有 score、recording、PCM、像素和参考身份。原记录、参考、评分算法与阈值保持原样。每条结论限定在冻结的小样本；不由样本外推 43 项的通过率。不会产生新的浏览器或 ASR 采集。本子任务仅交付此文件。

## 实验计划（先于聚焦分析）

### 离线核查 1：冻结样本并核对可归属证据

- **目标**：从 43 项 unknown 的 waveform、无文本、transition 原因中，各冻结至多 2 个代表项（优先最早项及不同时段的同类项；类别不足时如实记录），用现有 PCM、可见字幕像素及独立参考身份判断原因。
- **待区分假设**：A. 评分保守边界或归属方法漏掉可测的正确内容；B. 实际输出字幕缺失或与语音/参考内容不符；C. 当前记录无法排除多种解释。
- **将改变的决定**：A 只有在独立内容与边界证据同时足够时才建议具体评分修复；B 指向相应产品路径的具体修复；C 保持 unknown，并列出缺少的最小证据。任何结果都不修改本轮分母或授予产品通过。
- **冻结方式**：先记录评分 reason 分布、选中的参考 ID 与原 reason，再读各自附近 PCM 和像素，禁止依据观察结果换样本。
- **停止条件**：本次为一个固定样本离线实验；只有出现可改变实现决定的新增证据才追加，累计最多 3 次。没有新决策证据即停止该路径。

## 证据与结果

### 冻结样本（读内容前确定）

按原 score 每类参考 ID 最小和最大的项取样，合并重复 ID；最终固定 **1、2、50、58、59** 五项。原 reason 的计数可重叠：waveform 25、visible text 19、pixel transition 11、reference voice uncertainty 3。原 VAD 未知 12/39/51 继续保留，本切片没有改判其边界。

| 类别 | 冻结 ID | 原 reason |
| --- | --- | --- |
| 波形 | 1、59 | `output_waveform_unmatched_or_ambiguous` |
| 文字 | 1、58 | `visible_text_unmatched_or_ambiguous` |
| 像素边界 | 2、50 | `pixel_transition_unbounded` |

以下分析只使用这五项及确定它们相邻边界所必需的相邻观察；不更换样本。

### 候选、输入与完整性

捕获和原评分候选为 `0260efb0bf32d4e17182797d429d48703e6fc753`，捕获记为 clean；对应应用来自 `a0e511e`。本次读取工具 worktree HEAD 为 `c189e50ebdef0b21663c738b5ea1e7130128cede`。当前评分脚本哈希与原评分运行记录完全相同，因此下述源码解释适用于原评分。原采集 UTC 时间为 2026-11-18T10:26:16，正常完成至 316.619 秒 EOF。[E1], [E2], [E3], [E4], [R1]

下表是本次实际重新计算的 SHA-256；源文件、参考、原评分、PCM 与账本都没有改写。PCM 和账本哈希同时等于原评分的 `capture_integrity` 记录。[E1], [E5]

| 对象 | SHA-256 |
| --- | --- |
| 固定中文 WAV | `d40bb3a49d88cf35f52574650d593573048f89f95aba09b704b2fdbe25811317` |
| 59 项参考 manifest | `fd511cd47f6221787f7325d6a935bd7bcf9bface55006822238a89388c8668b1` |
| 原 voice spans | `ed5c5f77c2010809cbc3c7143dd44ad1bcd693a09d17587fb18da3c09a7e4aa6` |
| 原 engineering score | `ee55f642e969d80018edc14d74625cdf0bbe25e9b3ce1f83d4a99e7471138311` |
| 原 recording | `4ec0210ce65bcb07f181fcd3737a30543df3ba5afc20631b37d4f3d68f403b8e` |
| 输出 PCM | `ff5eee44cc9dda74c831c983d1a22004d5e86af418d0ebe0efd24d2da5826c16` |
| PCM/时钟/像素账本 | `3dd11c1990095da8a5cea23ecf4b3daeb0a3d802222abb53e118fc930ccba43e` |
| 评分脚本 | `d2d4a8c206d75623a8943d64558e812734686518e038cff96c5e18bade3616d5` |

每项身份由冻结 manifest 的顺序、来源 ID 和源素材区间确定；可见字幕与 ASR 回调没有用于重新定义参考。五项身份如下。[E6]

| 项 | 来源 ID | 源素材区间（秒） | 参考 |
| --- | --- | --- | --- |
| 1 | `SSB00050001` | 0–9.4443125 | 广州女大学生登山失联四天警方找到疑似女尸 |
| 2 | `SSB00050002` | 9.6943125–13.23025 | 尊重科学规律的要求 |
| 50 | `SSB00050051` | 257.7369375–259.9319375 | 一路向北 |
| 58 | `SSB00050060` | 303.7960625–310.717 | 自从二零一四年涉足跑步领域以来 |
| 59 | `SSB00050061` | 310.967–316.619 | 供应的增加会加剧积压的风险 |

### 五项分类

这里的“评分假阴性”仅指已有内容或有限采样区间未被评分表示；每一项的正式计量状态仍使用原 score。文字归属、波形归属和边界分别判断，原因可重叠。

| 项 | 实际像素与原评分 | 证据支持的分类 | 仍缺什么 |
| --- | --- | --- | --- |
| 1 | 图 50 显示“广州女大学生登山失联4天，警方找到疑似女尸。”；参考为“四天”。原 `caption_occurrences=[]`，三段波形中第二段未匹配 | **文字归属假阴性：数字显示等价**；原字面匹配规则按预期保留 unknown。整项继续 unknown | 第二段波形身份与像素边界；数字等价自身不能授予同步 credit |
| 2 | 图 159 为“尊重科学规律的要求。”，图 160/161 均为空白；原波形唯一匹配、文字唯一匹配，消失边界 null | **评分边界信息丢失**：有有限的保守采样外包区间 | 将该有限区间纳入独立审查的评分候选，保留截图不确定度及采样假设 |
| 50 | 图 2354 为空白，2355/2356 为“一路向北。”；原波形唯一匹配、文字唯一匹配，出现边界 null | **评分边界信息丢失**：有有限的保守采样外包区间 | 同上；区间宽度必须如实保留 |
| 58 | 图 2766 显示“自从2014年涉足跑步领域以来。”；参考为“二零一四年”。原波形与文字均未匹配 | **文字归属假阴性：数字显示等价**；整项继续 unknown | 整段波形身份；数字等价本身不能证明音画归属 |
| 59 | 图 2829 显示“供应 的 增加 会 加剧 积压 的 风险。”；原文字与起止像素边界已匹配，两段波形未匹配 | **unknown**：画面有对应文字，声音归属没有通过原门槛 | 两段输出波形的可确认身份 |

表中像素已实际查看，并按账本逐图校验哈希；空白图使用同一 SHA-256 `eca37010f3f9f5c459a2094b3c0b54fd48c7d5b9aa7f21177e81982fa1f2acef`。图 50、159、2356、2766、2829 的内容分别与原 score 页 1、2、54、64、65 一致。[E1], [E5], [P1], [P2], [P3], [P4], [P5], [P6]

原文字算法先做 NFKC、去标点和空白，然后逐字精确匹配；它明确保留数字的书写形式。“四/4”和“二零一四/2014”因此没有相同 token。这解释两项文字 reason，不能外推另外 17 项都是同类，也没有在本次加入数字归一化或更改原 reference。[S1], [S2]

### 像素边界为何变成 null

`_pages` 把 `dom_state_stable=false` 的观察标为 `tokens=None`，紧邻此观察的页面起止直接置 null。样本 2 的观察 160 正好是消失转场，样本 50 的观察 2355 正好是出现转场；它们各自前后仍保存了稳定的不同状态图。[E5], [S1]

| 项与边界 | 前一稳定观察 | 中间不稳定观察 | 后一稳定观察 | 两稳定观察中点差 | 保守外包边界（秒） |
| --- | --- | --- | --- | --- | --- |
| 2 消失 | 159：字幕；[17.9219,17.9524] | 160：空白；[18.0409,18.0875] | 161：空白；[18.1435,18.1702] | 219.70 ms | [17.9219,18.1702] |
| 50 出现 | 2354：空白；[264.6163,264.6418] | 2355：字幕；[264.7349,264.8188] | 2356：字幕；[264.8563,264.8947] | 246.45 ms | [264.6163,264.8947] |

这里取稳定前观察的 bracket 下端到稳定后观察的 bracket 上端，中间观察继续 unknown。两稳定观察的中点差均未超过原 250 ms 像素观察间隔屏障；50 的完整外包区间宽 278.4 ms，包含截图自身的不确定度，没有将其缩成 250 ms。以上只是沿用采样解释时可保留的保守区间，观察间的短暂闪变及物理显示时刻仍未知。[E5], [S1]

沿用原音频边界区间，仅作诊断相减：2 的消失误差可写为 `[0.178920,0.575321]` 秒；50 的出现误差可写为 `[-0.211169,0.195365]` 秒。2 的上端仍超过原 0.5 秒 P95 门槛，有限区间本身不表示同步通过。[E1], [E5]

补核对两组三图：每组观察号均连续，恰好一个中间不稳定图。160 与稳定端点 161 的 PNG SHA-256 完全相同，为空白；2355 与稳定端点 2356 的哈希完全相同，为“一路向北。”。两个中间图都已实际查看。原 `_pixels` 按图片哈希缓存 OCR，因此它们在原运行中的 OCR 必然与相同哈希的稳定端点一致，未出现已知第三种文字状态。[E5], [S1], [P3], [P4], [P7], [P8]

六图的 context ID、viewport 1280×800、DPR 1、固定 ROI `(282,589,960,118)`、OCR crop `(0,0,960,118)`、8 px shadow margin 和 bottom 的 1/64 px 规范化规则均一致。2 的字幕框从 `(597,640.703125,330,57.5)` 转为空；50 从空转为 `(672,640.703125,180,57.5)`，非空框均在固定 ROI 内。唯一已记录的框变化就是这次空白/字幕切换，原 raw bottom 的 64.8/64.7969 px 差异仍按既有规则处理。两段外包窗口内没有已记录的 media 控制事件或 mapping barrier；六个 mapping barrier 都发生在起播前 0.354 秒以内。字体配置来自原固定 capture descriptor，账本没有逐图独立字体族审计，结论限定为现有捕获契约下的几何一致。[E5]

### 波形 unknown：保持 0.99，读出失败的距离

使用已有 worker `/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python`，按原 `_read_audio`、`_captured_samples`、`_compact` 在 16 kHz 形成相同参考和输出；沿用 `_WaveformScan.matches` 的全输出归一化相关公式，额外只打印每段最大相关值及原 `>=0.99` 兼容区间数。脚本只读数据、向工具日志输出，没有执行评分 CLI 或保存新 score。下表 `context` 是音频上下文位置，不能替换浏览器输出时钟或物理声学时刻。[S1], [S3]

| 项/有声段 | 固定参考 span（秒） | 全输出最大相关 | 原 ≥0.99 兼容区间数 | 最大值处 context 起点（秒） |
| --- | --- | --- | --- | --- |
| 1/1 | 0.610–3.230 | 0.994622250 | 1 | 5.0526875 |
| 1/2 | 3.394–6.078 | **0.988649410** | **0** | 7.8366875 |
| 1/3 | 6.274–9.086 | 0.992155288 | 1 | 10.7166875 |
| 2/1 | 10.208312–12.860313 | 0.996408609 | 1 | 14.6510000 |
| 50/1 | 258.282938–259.494937 | 0.993293773 | 1 | 264.3976250 |
| 58/1 | 304.310063–310.290062 | **0.986335376** | **0** | 310.4247500 |
| 59/1 | 311.449–314.229 | **0.989211434** | **0** | 317.5636875 |
| 59/2 | 314.361–316.181 | **0.986789263** | **0** | 320.4756875 |

这些最大值解释了原空列表；它们没有达到冻结身份门槛。因此 1/58/59 继续 unknown，不能据此记为“确认缺失”，也不能将接近门槛的候选直接认定为正确声音。当前数据未区分重采样/采样相位影响与实际波形变化；要改变这三项结论，仍需要在原门槛下解释输出与参考的对应关系。[E1], [S3]

## 下一切片与停止点

**可以独立审查一个 `_pages` 边界格式化修复。** 它只为下述窄情形保留两端完整 bracket 形成的外包区间及观察 ID；不稳定 run、`unknown_gap_before` 和 `_caption_occurrences` 的跨未知拼接屏障全部保留。这样单页 2/50 可以获得保守边界，同时跨这个未知 run 的多页文字匹配继续被禁止。音频唯一身份、原误差阈值和覆盖计算的其他条件照旧。[S1], [E5]

| 判断点 | Go：允许形成候选外包边界 | No-go：继续 null/unknown |
| --- | --- | --- |
| 观察数量 | 原始连续三图，仅中间一图不稳定 | 缺图、合成间隙、连续多图未知 |
| 端点状态 | 两端稳定；一端为空白、另一端为单个已确认文本页 | 两端均为不同非空页、同状态或任一 OCR 未知 |
| 中间图 | 已知 OCR 属于端点之一；本切片最窄修复可直接要求其 PNG 哈希等于一个端点 | 已知第三种文字/图像状态，或无法判别 |
| 时间 | 两稳定图观察中点差 ≤250 ms；完整 bracket 如实保留 | 跨过原 250 ms 屏障，或人为收窄截图不确定度 |
| 几何与控制 | 同一 context、viewport、DPR、固定 ROI/crop/规范化契约；字幕框仅作此次空白/文本切换；窗口内无已记录控制/时钟屏障 | 移位、缩放、字体/布局重配、context/device 变化或控制/时钟屏障 |
| 归属 | 目标文字仍须唯一匹配，音频仍须原 ≥0.99 唯一匹配；跨未知的多页匹配继续拒绝 | 文本/音频共享身份、重复/重播歧义或依赖跨未知拼接 |

这两组现有证据满足上述边界候选的窄条件；未观察闪变仍属于采样限制。实现审查应保留过长间隔、连续未知、已知第三状态、两非空页、几何变化、跨页与重播反例。此文件没有实施或执行该修复。[E1], [E5], [S1]

文字的两项数字等价应在诊断中单列，原固定字面归属规则暂保留。三个波形问题的最小缺口是输出波形变化的独立解释；本次没有新增能决定该解释的证据，停止这一路径。单独修好两项像素边界，候选可测数也最多从 16 增至 18，离 50 句仍差 32 句，不能解决整体覆盖门槛。[E1]

## 本轮交付与验收状态

| 层面 | 本轮结论 |
| --- | --- |
| 代码 | 本子任务仅新增此 note；原评分脚本、reference、voice spans 和 captures 保持原样；没有 commit |
| CI | 本轮未执行测试或 CI；#105 当前仍记录两项完整 CI 失败，分别是无识别推进 15 秒停止与 source read 阻塞期间暂停预算 |
| 运行时 | 只使用 2026-11-18 已完成的 fixed02；没有浏览器/ASR 调用或服务变更 |
| 产品 | 原 16/59、43 unknown 和采样覆盖 `[21.2126%,99.7790%]` 保留；至少 50 句、P95≤0.5 秒、最大≤1 秒和覆盖≥95% 尚未通过；#70 formal FAIL/UNKNOWN 保留 |

父任务中的本地长片 EOF/缓存跳转、双语各 30 分钟、连续性/长空窗/资源及充分同步覆盖继续开放。此小样本结论只支持上述五项对应候选和日期。[I1], [I2], [R1]

## 一手来源

- [E1]：原 engineering score，重点为 `items[0/1/49/57/58]`、`visual_evidence.pages`、`thresholds`、`coverage`。
- [E2]：原 recording；[E3]：采集 manifest；[E4]：原评分运行记录；[E5]：PCM/时钟/像素账本。
- [E6]：冻结中文参考 manifest；[E7]：冻结 voice spans。
- [S1]：工程评分源码，`_pages`、`_caption_occurrences`、`_measure`；[S2]：固定文字 token 规则；[S3]：波形扫描源码。
- [I1]：#105；[I2]：父 Issue #99；[R1]：原固定参考报告。Issue 状态为 2026-11-20 读取时的记录。

[E1]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.engineering-score.json
[E2]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.recording.json
[E3]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.manifest.json
[E4]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.engineering-score.run.json
[E5]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.playback.frames.jsonl
[E6]: ../research/original-caption-reference-manifests/zh-reference.json
[E7]: ../research/original-caption-voice-spans/zh.json
[S1]: ../../tests/acceptance/score_engineering_sync.py#L336
[S2]: ../../tests/acceptance/score_original_acceptance.py#L17
[S3]: ../../tests/acceptance/score_engineering_sync.py#L416
[P1]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-000050.png
[P2]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-000159.png
[P3]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-000161.png
[P4]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-002356.png
[P5]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-002766.png
[P6]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-002829.png
[P7]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-000160.png
[P8]: /media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/zh-engineering-fixed02.pixels/pixel-002355.png
[I1]: https://github.com/AiKiAi-stack/Lisca/issues/105
[I2]: https://github.com/AiKiAi-stack/Lisca/issues/99
[R1]: 2026-11-18-browser-clock-reference-results.md
