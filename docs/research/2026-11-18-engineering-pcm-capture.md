# 浏览器工程同步采集桥

日期：2026-11-18。范围依据 [ADR-0011](../adr/0011-browser-clock-engineering-sync.md)。

## 代码与证据

`--capture-engineering-sync` 为本地文件和直接 HLS 增加独立采集路径：实际可听媒体元素依次连接透明 AudioWorklet 与 destination；同一输出样本按全部声道、交错 Float32 little endian 流写到指定磁盘。每批保留实际处理量子的帧位置与长度、序号、文件位置、字节数和哈希，尾批计数必须与 worklet 一致。通道、格式、序号、帧或字节不一致以及队列溢出返回 `unknown`。

原始输出时间戳、调用括号、上下文状态及设备变化逐条写入同一 JSONL。上下文暂停、设备变化及不可用时钟形成映射屏障。媒体暂停另记。结果使用 `measurement_scope: browser_output_timestamp_estimate`，API 误差界保持 null，物理输出延迟保持 unknown。

工程路径按约 100 ms 目标周期保存固定字幕区域 PNG，空白期间继续采样。区域由播放器和两行字体几何计算，保留 8 CSS px 阴影余量；每次检查字幕实际边界完整容纳且播放器、字体和视口几何未变。每张图保留前后浏览器时间、哈希和完整裁切几何，DOM 文本仅供诊断。详细字段和运行命令见[采集指南](../guides/real-browser-collector.md)。旧采集路径未改变。

## 限定浏览器自检

Chrome `156.0.8078.47`，现有安全源 `127.0.0.1:8002`。导航后用合成页面替换应用 DOM，独立 Start 按钮播放 IEEE Float32 双声道 WAV；拦截识别请求。未改动服务或配置。

本次最多三次候选采集，原始记录位于 `/media/wbb/KEKE/LiscaAcceptance/engineering-pcm-capture-20261118/`：

| 候选 | 结果与范围 |
| --- | --- |
| probe-1 | 自检样本期望失败：PCM16 解码正值按 32767 归一化，负值按 32768；原始样本和失败报告保留。流写、帧连续、哈希、暂停及尾批检查通过。 |
| probe-2 | 使用独立 Float32 载体；字面样本值、双声道、起始信号、帧、哈希、尾批、暂停和实际截图检查通过。 |
| probe-3 | 在相同 PCM 检查上加入周期区域截图，实际保存 blank → First visible words → blank → Second visible words → blank。采集桥自检通过，识别请求为 0。 |

最终 probe-3：48 kHz、双声道、84,480 帧、21 批、675,840 字节，队列峰值 1/4，无完整性错误；上下文状态为 suspended/running/suspended/running。22 张 800×112 PNG 共 61,268 字节，观察中点相邻间隔 85.1–135.65 ms；实际时间括号保留。`self-check.json` 冻结采集 Python、JS、区域截图及 collector 的源码 SHA-256，交付时核对。

PCM SHA-256：`b184a6dc470508daa64d78a171526b7ba4409738c40bbf8e3e19ee215c37c754`。
JSONL SHA-256：`7a3c6630672eb10918bed3715a355083aec93beb10d9796cabf688022078b378`。

## 结论边界

- 代码：无损流写及周期图像路径已完成，缺块、格式变化和缺尾会保留 unknown。自动回归覆盖文件、CLI、报告与区域边界，原固定参考采集回归保留。
- CI：本切片未推送；本地回归结果不作为远端 CI 状态。
- 运行时：上述合成 Chrome 自检支持其采集完整性范围；实际应用在 Start 后才装载来源的流程、长时磁盘负载与直接 HLS 仍由真实运行留证。
- 产品：API 时间戳精度没有新的数值保证；保存的图像仍需独立 OCR。未执行付费识别、旧校准或真实长测，本记录不授予同步、覆盖率或正式发布通过。

[W3C Worklet 输入契约](https://www.w3.org/TR/webaudio-1.1/#dom-audioworkletprocessor-process)规定无活动来源时输入数组为空，采集会等待活动输入后建立首帧。该契约说明启动等待的处理方式；自检范围仍按实际执行的合成路径报告。
