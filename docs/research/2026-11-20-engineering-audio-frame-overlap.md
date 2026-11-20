# 英文工程采集的 128 帧位置重叠

日期：2026-11-20。范围：#105 采集工具调查；既有英文证据、上游规范和源码，以及随后授权的一次零 ASR 诊断回放。未启动或重启服务，未产生新的产品验收通过结果。

## 决定

**一次新增观测量的零 ASR 诊断已完成，未复现；根因仍为 unknown，停止该回放路径。No-go：直接重跑付费英文验收、把重叠帧重排为连续时间，或将现有失败报告改为通过。** 现有证据已能确定位置重叠及拒收批次的来源；仍不能在 Chromium 时钟更新与采集器记账之间确定根因。以下 Chromium 条件更新机制为新假设，本次仅检验其能否在隔离图中自然复现。

## 已有证据

失败候选为 `0260efb0bf32d4e17182797d429d48703e6fc753`；本次工具工作树为 `c189e50ebdef0b21663c738b5ea1e7130128cede`。两者的 `browser_pcm_capture.js` SHA-256 都是 `6b529c56b6fd48c1c144886f4496e8ef2b2230f159d908243804c5bba5a192d6`。英文输入 `en-single-speaker-reference.wav` SHA-256 为 `4b7d55148fed309e1d71d941d75e89a64b1068a1473163f5f6a9ac4ab6fd7523`。[录制身份](/media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/en/en-engineering-fixed02.recording.json)、[报告](/media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/en/en-engineering-fixed02.report.json)

| 观察 | 原始位置或结果 |
| --- | --- |
| 已保存批次 | sequence 0–404，12,960 个 quantum；区间 `[0, 1658880)` 连续 |
| 首个 worklet 错误 | ledger 第 1425 行：`current_frame=1659776` |
| 尚未提交的批次 | 第 1435 行拒收的 sequence 405：8 × 128 帧，起点 1658880，末 quantum 起点 1659776，末端 1659904 |
| 位置关系 | 报错值等于末 quantum 起点，比预期下一起点少 128 帧；48000 Hz 下约 2.667 ms |
| 最终差额 | worklet 1,659,904 帧与已保存 1,658,880 帧相差 1024；恰等于拒收的部分批次 |
| 结果 | `recording_status=failed`、音频 `unknown`、未观察到 EOF；英文 67 个固定身份仍未计量 |

来源：[原始 ledger](/media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/en/en-engineering-fixed02.playback.frames.jsonl)、[2026-11-18 完整性审计](/media/wbb/KEKE/LiscaAcceptance/caption-reference02-20261118/en/en-engineering-fixed02.integrity-audit.json)。原 ledger SHA-256：`4cef7eb9660a81e33c1c8972671c2a0edd92a4d3160fa812bc919a4327f6e30e`；PCM SHA-256：`b1551de0dd74d79effe6531cab17e16f263d1460aa98b4c8a0f0e20955f7ca3c`。这些是保存证据中的身份，本次没有重新读取整段 PCM 计算哈希。

采集器在 `process()` 内先以 `currentFrame !== this.endFrame` 拒绝不连续位置，再复制本次样本；`endFrame` 仅在接收 quantum 后设为 `currentFrame + 实际 output 长度`。`capture_error` 立即使 Python writer 无效，随后 stop 刷出的 1024 帧被 `capture_already_invalid` 拒绝。拒收元数据存在，拒收样本字节未落盘。因此差额有明确的采集流程解释，不能据此断言播放声音缺失 1024 帧或重复 128 帧。[worklet](../../tests/acceptance/browser_pcm_capture.js)、[writer](../../tests/acceptance/browser_pcm_capture.py)

## 规范与对应浏览器源码

Web Audio 1.0 规定，worklet 的 `currentFrame` 等于所属 context 的当前帧；渲染一块音频后，context 当前帧增加一个 render quantum。故连续调用的帧位置应与实际块长一致。此规定说明预期语义，不能证明本次浏览器实际遵守。[currentFrame 定义](https://www.w3.org/TR/webaudio-1.0/#dom-audioworkletglobalscope-currentframe)、[渲染算法](https://www.w3.org/TR/webaudio-1.0/#rendering-loop)

英文失败 run 本身没有保存浏览器版本。相邻的 2026-11-18 `probe-1/2/3` 与 `green-3` mono/stereo/HLS 启动检查均保存 `156.0.8078.47`；green-3 也保存了同一 capture JS 哈希。这只将该版本与相邻检查关联，不能严格补全英文失败 run 的版本身份。[相邻检查](/media/wbb/KEKE/LiscaAcceptance/engineering-app-startup-20261118/green-3/mono/startup-check.json)、[probe-3](/media/wbb/KEKE/LiscaAcceptance/engineering-pcm-capture-20261118/probe-3/self-check.json)

Chromium **156.0.8078.47** 对应源码存在具体机制：destination 先推进 `CurrentSampleFrame`，再调用 `UpdateWorkletGlobalScopeOnRenderingThread()`；该函数用 `GraphAutoTryLocker`，只在取锁成功且线程匹配时更新 worklet 的帧值；getter 直接返回保存的 `current_frame_`。**推断：** 某次更新被跳过时，下一处理调用可能看到旧帧值，即使音频样本已推进。此源码路径支持“浏览器暴露的位置暂时陈旧”的假设；既有 ledger 没有锁状态或后续 quantum，无法证明本次走过该路径。[destination 源码](https://raw.githubusercontent.com/chromium/chromium/156.0.8078.47/third_party/blink/renderer/modules/webaudio/realtime_audio_destination_handler.cc)、[条件更新源码](https://raw.githubusercontent.com/chromium/chromium/156.0.8078.47/third_party/blink/renderer/modules/webaudio/base_audio_context.cc)、[getter 源码](https://raw.githubusercontent.com/chromium/chromium/156.0.8078.47/third_party/blink/renderer/modules/webaudio/audio_worklet_global_scope.h)

## 最小诊断回放提案

原验收项：无损 PCM 与浏览器帧位置完整对应，才能进入 ADR-0011 的字幕起止同步估计。待区分假设：H1 浏览器重复暴露 `currentFrame`、H2 采集器状态或消息桥重复/错误记账、H3 声音样本本身重复。结果决定先修哪层；不会直接放行字幕验收。[ADR-0011](../adr/0011-browser-clock-engineering-sync.md)

1. 使用 8002 既有服务上的隔离诊断页面、相同 headless 启动方式与 media → worklet → destination 路由；拦截并计数所有 ASR session POST，要求为零。新目录保存浏览器完整版本、可执行文件身份、候选和脚本哈希。当前检查仅播放约 1.2 秒，不能排除本次约 35 秒时出现的问题。[既有零 ASR 工具](../../tests/browser/check_engineering_pcm_capture.py)
2. 每次 `process()` **入口**只读一次原始 `currentFrame`，独立递增 `invocation_id`，记录实际输入/输出块长、前一观测帧、前一期望末端、累计收到的样本数量。累计量只标为捕获顺序，不用作替代时间戳。保留异常前后有界 PCM、每块哈希和调用编号；异常样本进入独立诊断证据，原验收 writer 继续判 unknown。增加桥接前后相同调用编号，区分消息重复与真实新调用。
3. 使用与 context 相同采样率、每个样本位置可区分的确定性 WAV，通过实际 media 元素播放，长度 60 秒。普通语音相同样本或静音可能自然重复，单看相邻语音块哈希不能证明 H3。也可另行使用冻结英文 WAV 做路由复现，但其无异常仅说明该次未复现。
4. 若新调用编号增加、原始帧重复而确定性样本按下一位置前进，则支持 H1；若入口帧连续而桥后重号或 `endFrame` 关系异常，则支持 H2；若样本也重复，则需继续保留 H1/H3，不据哈希直接宣布浏览器 bug。`currentTime` 与 `currentFrame` 在 Chromium 来自同一字段，不能作为独立时钟证明。

失败应在首个异常后保存短窗口并停止；若 60 秒未复现，结论为 **未复现，根因 unknown**。既有 probe-1/2/3 已构成三次桥接检查，不能继续原样重复；本提案必须新增上述调用身份与异常样本观测才有决策价值。同一诊断问题累计最多三次，没新增证据提前停止。

## 单次诊断执行结果

运行前保存[实验计划](/media/wbb/KEKE/LiscaAcceptance/engineering-frame-diagnostic-20261120/plan.json)。2026-11-20 12:31:01–12:32:03 UTC 执行一次，外部进程硬上限 78 秒。使用工程工作树 `c189e50`、实际 Chrome `156.0.8078.47`、48000 Hz AudioContext、60 秒真实 media 元素 WAV。WAV 由 seed `0x13579bdf` 的 xorshift32 序列生成，SHA-256 为 `17ee911c9041d55b2d18c9c8422f89b08f5862645be335d7e8f2eecb5fc8976c`；生成算法和源码保存在独立证据目录。[执行脚本](/media/wbb/KEKE/LiscaAcceptance/engineering-frame-diagnostic-20261120/run_diagnostic.py)、[结果](/media/wbb/KEKE/LiscaAcceptance/engineering-frame-diagnostic-20261120/result.json)

| 诊断观察量 | 结果 |
| --- | --- |
| 独立 `process()` 编号 | 0–22507，共 22508 次；连续 |
| 入口原始帧值 | 从 0 到 2880896，每次 128 帧；无重叠/间隙 |
| 捕获样本数量 | 2881024；包含 media 边缘的 context 输出，不能当作源内容时长 |
| 桥接包序号 | 352 包连续 |
| ASR session 请求 | 0 |
| 结束信号 | `media_ended`，无处理器错误 |
| 保留 PCM | 无异常，保留末 32 个 quantum，32768 字节；没有创建异常时间戳 |

来源：[逐调用 ledger](/media/wbb/KEKE/LiscaAcceptance/engineering-frame-diagnostic-20261120/callbacks.jsonl)、[只读审计及哈希](/media/wbb/KEKE/LiscaAcceptance/engineering-frame-diagnostic-20261120/audit.json)、[有界 PCM](/media/wbb/KEKE/LiscaAcceptance/engineering-frame-diagnostic-20261120/bounded-window.f32le)、[PCM 块元数据](/media/wbb/KEKE/LiscaAcceptance/engineering-frame-diagnostic-20261120/bounded-window.json)。未执行第二次回放。

隔离页面由 Playwright 在 `http://127.0.0.1:8002/` 路由中直接提供；页面没有加载应用脚本，所有其他 HTTP 请求均中止，未到达生产应用或云服务。该轮没有复现原录制的输出时钟轮询、字幕像素截图及 ASR 负载，也没有直接观测 Chromium 锁状态。因此 **未复现不排除原负载下的条件更新问题，也不能排除原采集器状态问题**；本次新增证据仅说明该浏览器在这一次隔离媒体图运行中，原始帧值、独立调用编号及桥接顺序保持一致。按原定停止规则保留 unknown。

## 保留的边界

本记录交付调查及一次独立诊断；生产代码修复、CI、原负载运行时复现、英文产品同步/覆盖验收均未完成。原失败目录保持原样；不得丢帧、重编号帧位置、补造时间戳或用累计样本数悄悄代替浏览器时钟。物理输出延迟、旧正式声学校准及原分母/门槛继续保持原结论。
