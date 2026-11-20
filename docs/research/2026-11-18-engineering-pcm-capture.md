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

## 静态审查 P2 修复

两项发现分别留证于外部 `engineering-pcm-capture-20261118/review-p2-*`：

- 产物覆盖：原 collector 在已有 PCM 引发失败后仍重写录制、报告与进度。工程入口现于浏览器启动前检查明确的自有产物清单，并独占创建持久标签预留；同标签并发、旧 PCM/ledger/图像/报告/录制/评分/进度及检查点均拒绝。运行器预建 manifest 与外部日志仍可使用。CLI 红灯保留旧证据被改写的现场；修复后逐字节不变且零会话请求。
- 设备信号：保留 AudioContext sinkchange，加入可用的 mediaDevices devicechange；两者各自递增时钟段并记录屏障，保存时解绑。安装及尾批元数据记录信号支持和 best_effort/unknown，系统输出路由保持 `not_independently_observed`。不读取设备身份或请求权限；无事件不能证明设备未变。

本轮仅执行 fake-browser/CLI 与 Node EventTarget 边界测试：90 项 Python 回归、2 项 Node 测试通过。新增测试先在原候选上得到预期失败，原始失败日志和产物另存；未增加第四次 Chrome 采集。PCM worklet 处理、流写格式及区域截图实现保持原有字节，前述浏览器证据只支持其原有范围；新增设备观察由 Node 测试支持，待真实采集继续记录。

## 完整应用 Start 后装载媒体：矩阵 FAILED

实际系列在 `597c989` 的完整应用启动流程中暴露两项采集缺陷：单声道来源在 frame 512 触发声道格式错误；字幕从隐藏变为可见时，computed CSS bottom 从 `64.8` 变为 `64.7969`，尽管播放器、视口和整数 ROI 未变，精确比较仍令截图停止。修复将 worklet 输入按 destination 声道数使用浏览器 speakers 规则混音，显式固定输出声道；bottom 按 1/64 CSS px 规范化，原始值与规则逐图保存。真实几何变化仍保留 unknown。

证据根目录为 `/media/wbb/KEKE/LiscaAcceptance/engineering-app-startup-20261118/`。baseline-1 保全原五场失败；RED2 使用完整应用真实 Start，在浏览器内模拟会话、WebSocket 与媒体路由并匹配原故障。早先夹具 route 方法遮蔽错误在导航前发生，单独保留于 `full-app-red-2.txt`。唯一修复候选 GREEN3 复用 RED2 输入，三场 PCM/像素持续采集及帧账本通过，mono 的 .125 精确复制到两输出声道，HLS 两声道逐帧相同。

**整体矩阵仍为 FAILED。** 两场本地输入触及 10 秒 local_timeout；stereo 与独立 PyAV AAC 解码参考的左声道 cosine 为 0.9986017，未达到夹具原定 >.999，右声道为 0.9993110；拟合增益分别为 .984327/.992999。原阈值、失败报告、音频和图像均保留，不追加浏览器候选或真实识别。矩阵中的单项 capture checks 通过不授予应用完成或产品验收通过。

GREEN3 在 HEAD `597c989` 的 dirty 修复树上执行，原矩阵版本字段不改写。逐文件源码哈希、测试树 diff 哈希与最终提交的映射另存外部 `full-app-tested-tree-before-freeze.json` 及交付审计；不能将其归属于 clean `597c989` 或声称最终提交已重跑浏览器。静态应用响应字节匹配 `a0e511e`；仅静态资源进入现有 8002，三次会话与 WebSocket 均由浏览器夹具模拟，服务端 ASR POST 为 0。定向 Python 93 项、Node 2 项通过。同步体验、时钟精度与正式发布结论继续保留原范围。
