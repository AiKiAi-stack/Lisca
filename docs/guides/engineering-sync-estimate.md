# 浏览器时钟的字幕同步工程估计

口径见 [ADR-0011](../adr/0011-browser-clock-engineering-sync.md)。
`tests/acceptance/score_engineering_sync.py` 只读已落盘的工程采集产物，报告写在
`engineering_sync` 下，`measurement_scope` 固定为
`browser_output_timestamp_estimate`。运行评分器不会启动播放器或识别请求。

## 输入与命令

先按[浏览器采集指南](real-browser-collector.md)保存
`--capture-engineering-sync` 产物。保留报告、原始 Float32 PCM、JSONL 帧/时钟/像素账本及
PNG 的相对目录关系。原始产物和评分结果存于仓库外。

固定参考评分需要一起提供参考清单、原始完整声音和 VAD 文件。例如在仓库根目录执行：

```bash
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python \
  tests/acceptance/score_engineering_sync.py \
  --report /absolute/capture/zh.report.json --language zh \
  --reference docs/research/original-caption-reference-manifests/zh-reference.json \
  --source /home/wbb/.local/share/stream-with-asr/checkouts/p0-live-source/uploads/acceptance-media/zh-single-speaker-reference.wav \
  --voice-spans docs/research/original-caption-voice-spans/zh.json \
  --ocr-executable /home/wbb/.local/share/stream-with-asr/notes/acceptance-execution-20261027/ocr-tesseract \
  --tessdata-dir /media/wbb/KEKE/stream-with-asr-models/tesseract/runtime/usr/share/tesseract-ocr/5/tessdata \
  --output /absolute/capture/zh.engineering-score.json
```

英文使用 `en` 和对应的英文三个文件。产品评分强制既有批准哈希与中文59条、英文67条身份。
`--fixture` 专用于独立合成控制，报告带 `fixture_only: true`。

没有冻结参考的长片或 HLS，可只提供 `--report --language --output`：仍验证 PCM/账本、PNG
哈希、尺寸与裁切身份，语音归属和同步结果保留 `frozen_reference_missing`。
提供 OCR 参数时还会重新读取保存的像素；缺 OCR 不授予像素文字或覆盖信用。

退出码：`0` 为已形成完整工程估计，`1` 为 `unknown`，`2` 为损坏或不兼容的证据。
退出码 `0` 本身不表示阈值满足。

## 如何得到估计

1. 校验 PCM 总哈希、字节长度、每批哈希/序号/格式、所有实际量子帧位置，以及最终 worklet
   计数。缺块、乱序、重叠、格式变化、非有限样本、错误记录和缺失尾部拒绝评分；不补零。
2. 每个固定 VAD 有声片段在**全部输出**中做完整波形归一化相关扫描，门槛预先固定为
   `0.99`。连续兼容样本偏移组成一个区间，所有不连续的兼容出现均保留。重复、不唯一、
   波形变化或无法匹配保持未知。匹配不读取应用播放时间、ASR 字时间或字幕偏差。
3. 为识别暂停后继续的同一波形，只能在匹配副本中移除至少20ms的全零样本段，并保存原始
   样本索引。落盘 PCM 不变，暂停持续时间仍在映射后的墙钟中。多声道保留在原始证据中；
   匹配使用均值单声道及参考采样率。解码、混音或重采样造成无法辨认时仍为未知。
4. 用同一 `clock_segment` 内邻近、前进的原始 `getOutputTimestamp()` 对作线性插值；相邻
   对间隔至多250ms，局部斜率须在0.9至1.1。禁止外推和跨 suspend、设备变化、时钟异常
   屏障插值。消息到达时间仅作审计。该区间仍是 API 估计：调用括号、采样量子、插值一致性、
   `baseLatency` 与 `outputLatency` 均不构成物理误差上界。
5. 实际 PNG 经哈希、视口、固定 ROI、阴影余量、捕获括号和 OCR 校验后，按连续像素观察
   分页；自然重复全部保留。参考全文必须由完整页序列唯一对应，共享身份或未知观察之间
   的拼接不授予信用。DOM 字幕与识别结果只是诊断数据。
6. 出现和消失各用最后缺席/首次出现、最后出现/首次缺席界定图像转场，分别减实际输出
   语音边界，得到有符号区间。P95 使用预先固定的 nearest-rank；报告绝对偏差区间与最大值。

## 读报告

- `items` 保存全部固定身份、全部波形与文字兼容出现、逐项未知原因和偏差区间。
  `item_accounting` 的 measured、missing_confirmed、unknown 合计为全部参考身份。
  无法匹配不自动断言内容缺失；`unmatched_*`、重播/歧义数量另列。
- `reference_voice_unknown_items` 保留中文12/39/51、英文10/54的原 VAD 起点未知；原参考
  有声分母与既有未知回退规则不变。缺失、重播、未知均不缩小分母，重播不重复加分。
- `coverage.sampled_voiced_coverage_*_estimate` 是主要工程覆盖估计，假设相邻兼容图像之间
  页面持续。`visual_evidence` 保存目标周期、实际最大间隔、像素哈希与页的观察索引。
  相邻观察超过250ms会切断连续性，未知 OCR 和未验证片段没有估计下界信用。
  `coverage.unconditional` 单独保留未观察瞬间造成的无条件覆盖未知。
  250ms仅是新像素工程估计的容许间隔；原DOM采样的50ms周期与最大100ms有效性分别保留，
  导入报告须显式提供整数 `requested_interval_ms: 50`；`original_dom_sampling` 无效仍使工程结果未知。
- `sampled_engineering_thresholds_satisfied` 仅判断本口径的至少50句、起止P95≤0.5秒、
  最大≤1秒和参考有声覆盖估计下界≥95%。任何未完成参考身份仍使整体为 unknown。
  `formal_gate_passed`、`sync_gate_passed` 始终为 null，`release_gate_passed` 始终为 false。
- `source_progress` 始终为 unknown；同样的输出 PCM/图像无法排除静音内部的跳过后重播。
  `physical_output_latency` 与输出时钟物理误差界继续未知。

新控制只检查上述声明范围：0.6秒有声暂停保留墙钟、实际像素支持的0.75秒滞后可检出、
相同 PCM/图像对静音源推进保持不可辨识。它们与旧正式 #70 校准分别留证。
