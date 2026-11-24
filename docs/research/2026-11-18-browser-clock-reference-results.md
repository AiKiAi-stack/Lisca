# 浏览器时钟固定参考：中文完成，英文计量中止

日期：2026-11-18。应用代码来自 `a0e511e`，采集与评分共同候选 `0260efb0bf32d4e17182797d429d48703e6fc753`，clean；功能分支含测试修复 `7db53d2`。工具 [Draft PR106](https://github.com/AiKiAi-stack/Lisca/pull/106) 基于 [PR104](https://github.com/AiKiAi-stack/Lisca/pull/104)，责任票 [#105](https://github.com/AiKiAi-stack/Lisca/issues/105)。口径见 [ADR-0011](../adr/0011-browser-clock-engineering-sync.md)。

## 准入和输入

首个五例系列的失败保持原结论。完整页面零 ASR 修复后的矩阵仍有 AAC 立体声对照未知和模拟 WebSocket 观测缺口；单声道 WAV 已独立验证输出样本精确保持。因此仅逐例准入相同类型的固定中文、英文 WAV，均为本输入真实尝试第 2/3 次；中文退出 0 且采集完整后才准入英文。原三组长时输入均为 3/3，没有新增调用。

每例现实时间上限 430 秒，6/10/15/2 秒预算不变，不循环、不截片头、不改变速度。中文 SHA-256 `d40bb3a49d88cf35f52574650d593573048f89f95aba09b704b2fdbe25811317`，时长 316.619 秒；英文 SHA-256 `4b7d55148fed309e1d71d941d75e89a64b1068a1473163f5f6a9ac4ab6fd7523`。参考身份保留中文 59、英文 67 及原 VAD 未知。

## 真实运行

| 项目 | 中文 fixed02 | 英文 fixed02 |
| --- | --- | --- |
| 识别会话 POST | 1 | 1 |
| 开播 | 4.803 秒 | 2.653 秒 |
| 首次可见字幕 | 5.600 秒 | 3.053 秒 |
| 完成 | 正常 end/completed，实际到 316.619 秒 EOF | 约 35 秒因音频计量失败取消 |
| DOM 采样 | 50 ms；最大 57 ms；有效 | 50 ms；最大 72 ms；有效 |
| 采集错误 | 0 | `playback_audio_capture_unavailable` |
| PCM | 15,496,192 帧、48 kHz、2 声道，完整性 validated | `worklet_frame_gap_or_overlap`，整体 unknown |
| 取消 | 无需操作取消 | DELETE 200；停止后恢复普通 WebUI |

中文未记录字幕裁切、溢出或缓冲样本；保存样本审计有 3 次暂停，暂停总时长及归因仍分别核对。DOM 有一次 `I.` 的两点观察跨度为 49 ms；原识别终稿本身为 `I.`、时间范围 159.1–159.2 秒，不能据此认定由调度积压制造，也不能据 DOM 授予实际像素持续时间结论。末页观察到“供应 的 增加 会 加剧 积压 的 风险。”，其参考音频归属仍未知。

英文已保留原 PCM、账本和图像。最后待提交量子为 `[1659776,1659904)`，随后又报告 currentFrame 为 1659776，构成 128 帧／2.6667 ms 的位置重叠。已落盘 405 批连续，最终差额 1024 帧恰为拒收部分批次；拒收的音频字节没有保存，实际样本是否重复及根因仍 unknown。未重试、补零或改写帧位置。

## 中文工程评分

评分器与集成候选逐字节对应，未使用 fixture flag，原 0.99 波形相关门槛、身份及分母不变。执行 53.26 秒，退出 1，表示正常形成 **unknown** 结果，未发现产物契约或完整性错误。

- 59 项：measured 16、unknown 43、missing_confirmed 0；不足至少 50 句。
- 出现偏差的 P95 绝对区间 `[0.040993, 0.770257]` 秒；消失偏差为 `[0.247266, 0.473470]` 秒。样本仅 16 项，不能推广为整段同步通过。
- 参考有声分母仍为 246.780245 秒；采样覆盖估计 `[21.2126%, 99.7790%]`，无法证明至少 95%。无条件覆盖继续 unknown。
- 2875 次实际像素观察、66 页，OCR unknown 0；像素最大间隔 161.4 ms，按其独立的 250 ms 屏障报告。原 DOM 50/100 ms 有效性单列。
- 未知原因可重叠：波形未匹配 25、文字未匹配 19、像素转场无界 11、原 VAD 起点未知 3（12/39/51）。无法匹配不自动断言内容缺失。

`sampled_engineering_thresholds_satisfied` 为 false；旧 `formal_gate_passed`、`sync_gate_passed` 为 null，发布 gate 为 false。浏览器 API 的物理误差上界和源推进未知保持，不重启 #70 旧路径。

## 原始证据与剩余责任

中文目录 `/media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/`，英文在其 `en/` 子目录。每例含 admission、manifest、report、recording、PCM、JSONL、PNG、资源和退出记录；中文另含 `zh-engineering-fixed02.engineering-score.json` 及运行日志。运行器位于 `/home/wbb/.local/share/stream-with-asr/notes/caption-final-20261118/run_zh_reference02.py`，重复准入由独占 admission 拒绝。

仍缺本地长片正常 EOF/缓存跳转、中英各至少 30 分钟的新候选完整观察、内容连续性和长空窗/资源结论，以及充分的同步/覆盖工程估计。长时输入已达 3/3，追加真实实验需按 AGENTS.md 获得明确例外。两份 PR 保持 Draft，未合并 main 或发布。

## 2026-11-18 CI 补丁准备记录

功能 `7db53d2` 和工具 `0260efb` 的远端 Pylint 均通过，完整 Test 尚未通过。保留 PR Test 运行；取消一份重复 push Test 后，日志显示进度已越过恢复测试，到整句提交测试之前停止。只读核对发现 `_session_events` 仍默认禁止等待推进假时钟，与新的累计等待逻辑不兼容。

已准备一个仅修测试的候选：等待始终推进假时钟，回调时间只能向前移动，移除失效开关并更新全部调用点；原整句、期限、英文碎片和修订断言不改。此次 CI 诊断已用完三轮，候选先静态审查，尚未执行额外回归或推送触发 CI。下一次离线验证需明确例外授权；此前针对英文连续性候选的额外验证授权不用于重置本路径。

## 2026-11-20 两项 CI 失败的接续

经批准的额外一轮在 `776b495` 上解除原整句测试挂起。完整 Test 仍有两项失败，按停止条件保留了原记录。[功能分支 CI](https://github.com/AiKiAi-stack/Lisca/actions/runs/44308046912) · [工程工具 CI](https://github.com/AiKiAi-stack/Lisca/actions/runs/44308049819)

新候选 `46b1564` 分开验证持续输入满 15 秒、非静音输入提前 EOF 且无可用字幕、短静音 EOF。新增的 EOF 回归在旧代码可重现误报 ready/completed。现在显示可重试或仅播放的准备错误。暂停回归使用活动时钟完成首块投送，在阻塞读取期间注入暂停时间，保留原暂停、恢复与取消断言。定向 Python 5 项、前端 170 项通过。完整本地 Python 在超时上限内未跑完，远端完整 CI 结论待核对。
