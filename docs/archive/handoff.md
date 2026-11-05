# stream-with-asr 开发上下文交接

> 历史交接存档，记录截至 2026-10-25 的状态。当前入口见[项目导航](../README.md)；下文代码、数据和命令路径仍以仓库根目录为基准。

更新日期：2026-10-25，Asia/Shanghai。交接对象：在**同一台双系统电脑的 Ubuntu** 上继续开发的 Codex。

这是上下文交接，不是迁移到另一台机器。项目、媒体、日志仍在共用磁盘上；进入 Ubuntu 后按实际挂载位置打开同一个仓库即可。下文路径默认相对仓库根目录，仓库外资料使用相对路径另行说明。不要因为 Windows Codex 的聊天上下文不可继承而重新克隆、重做已有功能或删除旧证据。


## 最新接续入口（2026-10-25）

产品代码已到 `4405842`，当前分支仍为 `feat/original-caption-release`；下文10月24日及更早的HEAD、服务和“尚未真实识别”描述是历史快照。接手时先读[实施状态](../plans/2026-10-23-original-caption-implementation-status.md)，涉及真实质量或长测时再读[真实验收记录](../plans/2026-10-24-original-caption-real-acceptance.md)与[支持边界](../research/2026-10-25-original-caption-support-matrix.md)。

本轮接续来源聊天为“登录 gh 并继续项目更新”（`01a16871-60bd-7253-ad52-f259a610c941`），自动化为`automation-2`。

本轮已修复HTTPS信任/重定向、累计送音频节奏、直播watchdog误用、音轨PTS间隙压缩、preview超行、尾页闪现及重排换行、late-ready回退和累计直播等待。最终Python222通过/1Windows跳过，Node102通过，离线完整浏览器9/9。真实五分钟中文CER7.87%、英文WER1.66%；中文65秒快语速CER4.67%，参考7条已由用户逐条确认。自动分数和回放均不替代独立声学同步；英文AMI候选因含糊未确认；新的[MIT连续83秒候选](../research/2026-10-25-english-fast-speech-second-replacement.md)已准备，等待用户同时核对自然速度、单人清晰和官方参考文字，未调用其识别。

普通8002服务已恢复，页面/本地资源HTTP200、fixture404，配置只在内存。当前进程以持久notes的 `webui-resumed-final-process.json` 与 `webui-resumed-final-health.json` 为准；历史PID不可直接停止。真实批次已结束，原始事件、失败证据、哈希及141条听审包保留在忽略目录 `logs/original-caption-acceptance/real-2026-10-25/`。本轮仅本地提交，未推送、未创建远端Issue/PR。

继续时先核对新的英文候选是否已经获用户试听/参考确认；前提具备后才做有上限识别。随后按09/10补独立50句声学起止/覆盖率、20次固定实时启动及修复后长测延迟，逐项更新证据。08/11继续按用户决定延期，12仍未通过。每项以实测结果为完成条件，保留失败与未确认项。

## 1. 接手后先知道这些

- **当前产品：原文字幕。** 中文语音显示中文，英文语音显示英文。默认自动识别语种，中文/英文提示可选。跨语言翻译已暂停在当前界面中的使用；不要因为分支或文档名含 translation 就重新启用它。
- **产品尚未通过公开发布验收。** 核心链路、局部真实样本和自动化已有成果，完整质量、长时稳定性、故障、安装及跨设备证据仍有缺口。
- **继续当前分支 `feat/original-caption-release`。** 本次核对的代码 HEAD 为 `714f9d8d0963b6a019604ca2a47d11622fd87112`。写本文件前主工作树干净。
- 本地 `main` 和缓存的 `origin/main` 在 `f714ec9`；当前 HEAD 相对该引用有 **17 个提交**。当前分支没有配置 upstream。本次没有联网刷新远端或推送，不能断言 GitHub 的实时状态；不要切回 `main` 后误以为最新功能丢失。
- WebUI 固定使用 **8002**。检查占用进程后再重启，只保留一个实例。不能另开 8000/8001 逃避旧进程问题。
- 正式需求入口是 [PRD v1.0](../plans/2026-10-21-live-translation-prd.md)、[ADR-0008](../adr/0008-original-language-captions.md) 和 [已确认实现规格](../plans/2026-10-23-original-caption-release-spec.md)。旧 SPEC、翻译稿、固定延迟方案不能覆盖这些决策。
- 12 项正式任务在 [.scratch/original-caption-release/issues/](../../.scratch/original-caption-release/issues)，**这些 Markdown 已跟踪进 Git**；不能把整个 `.scratch` 当垃圾清空。
- 用户已将 **08 局域网第二设备验收、11 干净 Windows 验收延后**。保留门槛，不当作完成；无需反复追问相同环境条件。

本文件依据当前可读取的 8 个项目会话、仓库提交、文档、任务文件和本地证据整理。10 月 23 日 Linux 续作主要依据已保存的代码和文档；无法读取的另一系统聊天记录不作完整恢复承诺。本文没有重新运行云端付费识别，也没有把历史测试数字当成本次重新验证的结果。

## 2. 用户真正要的产品与协作方式

目标用户是**公开发布后的陌生用户**，不是仅供开发者演示。首版仍以 Windows 本机运行、浏览器观看为产品范围；开发主机改用 Ubuntu，不等于自动增加 Linux 安装产品化承诺。

首版使用用户自己的云端 API Key，音频上传到服务商，由用户承担费用。直播链接、本地视频都要支持；直接媒体流、频道列表和平台页面分别验收。当前沿用百炼中国内地 FunASR，不要求用户安装本地模型或 GPU 环境。

用户最在意的是**正在说的内容确实出现在视频上，且与当前声音对应**。历史区有文本、API 成功、SRT 生成，都不能单独证明观看体验通过。曾反复出现并被用户指出的失败包括：

- 历史区持续输出，画面字幕为空或一直显示“准备中”。
- 快语速或长句直到结束才一次性倾倒，随后快速消失，遮住后面的内容。
- 正式字幕逐字/逐词跳出；这不是用户希望的主画面。
- 修订只更新历史，不更新叠层；撤回文本被迟到结果复活。
- 原文、译文和播放时间脱节，例如 23 秒仍显示前面一句的英文。
- 画面、声音与字幕时钟不同，靠不断停播或倒退制造表面同步。
- 太多按钮、配置框、记录框同时占屏；默认页面应聚焦视频，设置、记录、频道按需展开。

低延迟是目标。用户明确纠正过：“几秒或十几秒延迟是工程问题，不是产品要求。”早期允许整体音视频缓冲，是当时的实现取舍，不是永久产品指标。当前应按 PRD 分别测首次字幕、定稿、应用附加延迟、来源缓冲和同步偏差。

协作约定：先明确需求和可观察验收，再实施；能调查解决的事不要反复问用户。用户曾明确要求不要用 DAG 交付产品代码，采用普通代码、提交、PR、审查、CI 流程。后续 12 项本地任务是已确认的规格拆分，不意味着恢复旧 DAG 执行。

新会话命名使用 `[YY-MM-DD] 会话主题`。Windows 下载优先非 C 盘；Ubuntu 也应留意实际可用空间。不要为本项目停止其他项目的 ComfyUI。只读调查和已授权的常规开发主动推进，不能把“继续”解释为允许无限付费测试或公开发布。

## 3. 文档导航与优先级

建议按以下顺序读：

1. [AGENTS.md](../../AGENTS.md)：8002、tracker、领域文档及代理模型约定。
2. [GLOSSARY.md](../../GLOSSARY.md)：草稿、稳定短句、终稿、共享会话和原文字幕术语。
3. [当前 PRD](../plans/2026-10-21-live-translation-prd.md)：R01–R09、A01–A10、数值目标和发布门槛。文件名保留 translation，正文已统一原文字幕。
4. [ADR-0008](../adr/0008-original-language-captions.md)：默认 transcribe、原文时间调度、显式翻译兼容边界。
5. [实现规格 / TR](../plans/2026-10-23-original-caption-release-spec.md) 与 [12 项任务索引](../plans/2026-10-23-original-caption-ticket-proposal.md)。
6. [逐项任务目录](../../.scratch/original-caption-release/issues) 和本文件第 6 节，确定实际剩余工作。
7. [中文使用说明](../../README_zh.md)、[README](../../README.md)、[界面设计记录](../guides/ui-design.md)，再按修改范围读代码。

其他有用资料：

- [10 月 23 日本地接续记录](../plans/2026-10-23-local-handoff.md)：`98b798b` 基线的节奏、静音、HLS 修复、Linux 回归和当时环境。开头“12 项均未完成”已落后于后续提交，不能直接复制为当前状态。
- [实施状态](../plans/2026-10-23-original-caption-implementation-status.md)：记录任务拆分和延后事项，但 01/02/07 仍写独立工作区实施中，03–06 写待实施；部分代码随后已经合入，需要核对更新。
- [L01–L06 本地 Issue 草稿](../plans/2026-10-23-local-issues.md)：保留问题来源；执行使用 01–12，不另建一套重复任务。
- [来源调研](../research/2026-10-21-media-source-baseline.md)：M3U、直接流、平台页的区分，候选链接及限制。属于 2026-10-21 调研，不能当作今天可播清单。
- [云端 API 调研](../research/2026-10-21-cloud-translation-api.md)：FunASR 时间戳、翻译 API 和其他路线；模型/协议信息是历史研究，重新接入前须核查官方资料。
- [旧翻译实现记录](../plans/2026-10-21-translation-implementation.md)、[PRD v0.3 归档](../plans/2026-10-21-live-translation-prd-v0.3-archive.md)：理解范围变更，不能作为当前验收标准。
- [早期 ASR 计划](../plans/2026-07-09-asr-subtitle.md)、[SPEC.md](SPEC.md)、[CONTEXT.md](CONTEXT.md)：历史入口。CONTEXT 仍描述早期逐字显示及隐式对齐，已不是当前权威行为。
- [UX round2](spec-webui-ux-round2.md)、[round4](spec-webui-ux-round4.md)、[round5](spec-webui-ux-round5.md)：历史 UI 切片。
- [FunASR API 资料](../reference/fun_asr_realtime_recognition.md)、[本地 Vulkan 文件模式](../guides/funasr-vulkan.md)：兼容能力参考。Vulkan 文档仍有 8000 示例，**开发时统一改用 8002**，不能照旧示例另起 WebUI。
- [tracker](../agents/issue-tracker.md)、[labels](../agents/triage-labels.md)、[domain](../agents/domain.md)：项目工作约定。

ADR 的演变顺序：

- [0001](../adr/0001-subtitle-display-model.md)：早期字级渲染，显示方案被后续取代。
- [0002](../adr/0002-subtitle-display-blocks.md)：固定窗口分块。
- [0003](../adr/0003-sentence-timed-subtitles-with-playback-delay.md)、[0004](../adr/0004-buffered-audio-video-and-final-captions.md)：句时间、整体音视频缓冲、稳定结果。
- [0005](../adr/0005-shared-lan-live-sessions.md)：只读局域网共享。
- [0006](../adr/0006-progressive-cloud-translation.md)、[0007](../adr/0007-readable-on-video-translation.md)：历史翻译、阅读队列和易读性。
- [0008](../adr/0008-original-language-captions.md)：当前原文模式；不把译文阅读队列用于原文调度。

遇到冲突：最新用户决策及当前 PRD / ADR-0008 确定范围；规格确定实现和验收边界；代码和实际证据确定落实程度。文档中有目标不代表代码已交付。

## 4. 已完成的主要工作与提交

### 4.1 早期媒体、ASR 与同步

项目原先是 `streamparser` 媒体工具包，具有流处理、转码、Pillow 烧录字幕和 SRT 管线；现在的浏览器观看产品是在其上继续建设。不要把工具包的能力列表直接当作当前 WebUI 支持矩阵。

已接入演示引擎、云端 FunASR、本地 FunASR GGUF 文件服务；完善过音频尾帧排空、异步错误上报、媒体时间戳、稳定短句、词时间分段、句子修订、频道列表识别和重新打开。英文单词曾被词时间片错误拆成 `wh istles`，修复方向是根据整句恢复文本并合并同词片段。

历史 [PR #26](https://github.com/AiKiAi-stack/stream-with-asr/pull/26) 合入过该阶段修复，历史回归记录达 78 项；这是当时版本证据，不是当前完整验收。

### 4.2 共享与 WebUI 优化

已建立主机一次识别、局域网只读观众加入的结构，最多 3 台观众，不含主机。观众从当前直播位置加入，自己的音量/暂停不改变他人；停止后旧链接失效，新一场生成新链接。管理 HTTP/WS 限制本机访问，分享不扩展为公网发布。

历史规格为 [#27](https://github.com/AiKiAi-stack/stream-with-asr/issues/27)，切片 [#28](https://github.com/AiKiAi-stack/stream-with-asr/issues/28)、[#29](https://github.com/AiKiAi-stack/stream-with-asr/issues/29)、[#30](https://github.com/AiKiAi-stack/stream-with-asr/issues/30)。随后修复观众断开后名额未及时释放、平台地址解析阻塞无关请求，对应 #31/#32，合入 [PR #33](https://github.com/AiKiAi-stack/stream-with-asr/pull/33)，`main` 基线 `f714ec9`。该轮历史记录为 95 项测试及 CI 通过、Windows 聚焦 8 项通过。

### 4.3 当前开发分支的演进

- `31053db`：Windows 启动、用户 Key 配置、云端识别接 Qwen-MT 的中英互译首版。真实短样本和 CGTN 曾跑通，但不是完整发布。
- `6ed2236`、`443211a`：视频内完整短句、收起配置/历史、全屏、暂停恢复、修订/撤回/迟到结果隔离。UI 使用原生 HTML/CSS/JS，没有引入前端框架。
- `9edc43f`：根据用户 `uploads/output.mp4` 的错误反馈，改为原文字幕优先；按音频时间调度，本地已完成识别后复用字幕。
- `c88e286`：PRD v1.0 收敛，旧翻译规划归档，明确验收门槛。
- `5a4dc43`、`98b798b`：直播最多累计等待 2 秒、本地 15 秒无进展处理、仅播放恢复入口；本地按 PCM 时长限制提前约 2 秒、暂停不积攒突发额度；静音进展、旧 HLS 回调隔离、HLS 恢复预算与评分基础。
- `074b46f`：已确认规格和 12 项正式本地任务。
- `44da45a`：离线识别替身 + 真实播放器验收入口，记录 08/11 延后。
- `e9e3064`，经 `b825dc4` 集成：录音实际输入身份、SHA、来源错配、文件替换、正常完成/取消、修订/撤回和评分 CLI 回归。
- `3f0ba5b`，经 `56dcde9` 集成：原文撤回/迟到结果防复现、两行短句与响应式显示修复。
- `37b87fd`，经 `532c505` 集成：原文会话创建识别器前的媒体验证，拒绝无音轨、不可解码及已知不支持输入；连接检查措辞区分“发起连接”和“真实识别成功”。
- `714f9d8`：补充窄屏选择 64px 字号及全屏的实际浏览器断言。**新增断言本身不证明已有对应运行证据。**

## 5. 代码地图和接口边界

- [tools/webui.py](../../tools/webui.py)：FastAPI 入口、管理权限、Key 配置、云端会话、HTTP 媒体代理、HLS 重写、上传、频道、主机与观众 WebSocket，以及音频投送工作线程。
- [tools/webui_static/index.html](../../tools/webui_static/index.html)：主界面与主要交互状态，媒体时钟、原文短句、暂停/跳转/换源、对话框和 HLS 事件。
- [caption_presenter.js](../../tools/webui_static/caption_presenter.js)：字幕展示辅助及历史翻译阅读队列；修改前确认当前分支走的是原文还是显式翻译路径。
- [playback_guard.js](../../tools/webui_static/playback_guard.js)：等待预算、无进展保护、降级状态；不能把发送音频或普通心跳当真实字幕进展。
- [player.css](../../tools/webui_static/player.css)：播放器、字幕、对话框与响应式样式；[hls.min.js](../../tools/webui_static/hls.min.js) 是本地分发依赖，许可见 [tools/licenses](../../tools/licenses)。
- [caption_preview.py](../../tools/caption_preview.py)：从累计 partial 提取稳定短句，处理前缀修订和剩余文本。
- [cloud_translation.py](../../tools/cloud_translation.py)：虽然名字含 translation，仍承载当前原文模式的 `CloudConfigStore`、`RequestStop`；同时保留显式翻译兼容实现和取消。
- [tools/engines/base.py](../../tools/engines/base.py)、[aliyun_funasr.py](../../tools/engines/aliyun_funasr.py)、[streamparser/asr_client.py](../../streamparser/asr_client.py)：引擎边界、DashScope `Recognition`、16kHz 音频及字幕事件。服务商毫秒时间转换到应用秒时间；需保留句身份、final、词时间和修订信息。
- [stream_source.py](../../tools/stream_source.py)、[input_resolver.py](../../tools/input_resolver.py)、[platform_resolver.py](../../tools/platform_resolver.py)：PyAV 解码/媒体时钟、输入类别、平台页解析。列表、平台解析、媒体拉流、浏览器播放错误必须分开。
- [live_share.py](../../tools/live_share.py)：主机共享会话、容量、链接和观众资源生命周期。
- [windows_launcher.py](../../tools/windows_launcher.py)、[start-windows.cmd](../../start-windows.cmd)、[requirements-webui.txt](../../requirements-webui.txt)：Windows 产品启动链路，Ubuntu 不运行 `.cmd`。
- [tools/stream_subs.py](../../tools/stream_subs.py)、[streamparser/subtitle.py](../../streamparser/subtitle.py)、[srt_writer.py](../../streamparser/srt_writer.py)：原有媒体工具链与烧录/SRT。
- [tools/funasr_gguf_server.py](../../tools/funasr_gguf_server.py)、[funasr_gguf.py](../../tools/engines/funasr_gguf.py)：历史本地整文件转写接口，默认 8765，不能当实时云端识别的替代验收。

当前公开会话接口：

- `GET/PUT/DELETE /api/cloud`：查询公开配置状态、更新和删除。返回 `configured` 等状态，不返回 Key。
- `POST /api/cloud/check`：只创建识别连接并清理，不创建翻译器。返回成功仅说明已发起连接，不能证明真实认证、完整语音识别或质量通过。
- `POST /api/cloud/sessions`：默认 `mode=transcribe`，`source=auto|zh|en`；随后 `/ws?session=...` 消费一次性会话。不要把会话 token 放到公开报告。
- `DELETE /api/cloud/sessions/{token}`：请求停止。拥有者断开、删除配置、仅播放等路径仍需系统验证无新投送/新请求。
- 旧翻译必须显式 `mode=translate`。仅传 source/target 不启用翻译；当前 UI 不传翻译模式。

## 6. 十二项任务：当前实际接续判断

以下将“已有代码”和“整项验收完成”分开。任务文件的 `ready-for-agent` 表示定义可执行，不是完成状态；部分状态文档尚未跟上提交。

1. [01 配置与安全打开](../../.scratch/original-caption-release/issues/01-configure-and-open.md)：媒体预检和连接提示已合入，两个检查项已勾选。完整配置到打开的真实浏览器旅程、未配置不识别、日志/持久存储/诊断脱敏等仍未形成整项通过证据。
2. [02 可读原文短句](../../.scratch/original-caption-release/issues/02-readable-timed-captions.md)：修订/撤回和响应式代码已合入，离线真实播放器有局部证据；完整中英显示/消失时间、快语速、无词时间、缺失区间、字号/全屏矩阵及闪页判定仍需补。任务清单尚未勾完。
3. [03 本地准备与静音](../../.scratch/original-caption-release/issues/03-local-preparation-and-silence.md)：已有 2 秒投送领先限制、暂停节奏和静音进展基础。尚无整项完成记录；继续验证未识别完即可播放、15 秒无真实进展停止、取消节流、30 分钟和容量边界。
4. [04 控制与隔离](../../.scratch/original-caption-release/issues/04-local-controls-and-isolation.md)：短样本已完成后暂停/跳转有零新增识别证据；未完成区间跳转提示/重建、3/30 秒暂停、连续跳转、换源、EOF 及迟到异步污染的完整旅程仍待验证。Linux 临时 controls 工作区也需先检查，避免重复或丢失未提交修改。
5. [05 直播等待与时钟](../../.scratch/original-caption-release/issues/05-live-playback-and-clock.md)：已有保护基础，尚无该切片完整交付记录。用可控本地 HLS 证明同次故障累计最多 2 秒，迟到结果不重置预算，不反复停播；恢复/重连对齐当前媒体。
6. [06 统一恢复与停止](../../.scratch/original-caption-release/issues/06-bounded-recovery-and-stop.md)：已有 HLS 两次恢复等局部修复，但需证明媒体和识别层共用预算、混合错误、1/2 秒退避可取消、停止/仅播放/删除配置/断开后的新请求为零、资源容量与释放。不能以某一层的测试替代统一预算。
7. [07 来源与评分](../../.scratch/original-caption-release/issues/07-evidence-provenance-and-scoring.md)：六项检查已全部勾选，代码和 CLI 回归已合入。任务仍标 `ready-for-agent`、状态文档仍写实施中；应复核已集成版本再同步状态。它只交付证据工具，不证明真实准确率或发布通过。
8. [08 局域网原文观众](../../.scratch/original-caption-release/issues/08-shared-original-captions.md)：**用户延后**，依赖 05/06。原有共享能力保留，尚缺本模式第二设备证据。
9. [09 中英本地质量](../../.scratch/original-caption-release/issues/09-local-quality-acceptance.md)：等待 01/02/03/04/06/07 和固定参考集、可用识别配置。真实准确率、实际画面同步、覆盖、长文件和冷/热启动分别留证；历史两处截图不能关闭任务。
10. [10 直播与频道](../../.scratch/original-caption-release/issues/10-live-and-channel-acceptance.md)：等待 01/02/05/06/07 及可达来源、参考样本、识别配置。仍保持一个任务，中文、英文、频道三个批次分别判定，不拆改已确认粒度。
11. [11 干净 Windows](../../.scratch/original-caption-release/issues/11-windows-install-and-privacy.md)：**用户延后**。双系统现有 Windows 和新建 venv 均不自动等同干净系统完整首装；DPAPI 必须在 Windows 实际验证。
12. [12 发布证据门槛](../../.scratch/original-caption-release/issues/12-release-evidence-gate.md)：**未通过**，依赖 08/09/10/11。延后项目不自动从 PRD 必验门槛移除。更新公开支持清单和用户说明后，再给明确发布结论。

推荐从 01/02/07 的集成复核与状态校准开始，然后补 03/04、05/06 的真实播放器行为；先用替身把行为稳定，再执行有边界的真实云端样本，避免反复付费重跑。

## 7. 验收标准和现有证据的边界

### 7.1 不能省略的 PRD 指标

- 中文和英文各至少 5 分钟清晰单人独立参考：中文 CER ≤10%，英文 WER ≤15%；快语速各另测至少 1 分钟，关键否定/数字/指代单独审阅。
- 每种语言至少 50 个短句：开始/消失偏差绝对值分别 P95 ≤0.5 秒，最大值 ≤1 秒；正常有声片段覆盖 ≥95%，缺失计入分母。
- 每种语言至少 20 次独立实时节奏启动：首次稳定短句延迟 P95 ≤3 秒；报告冷/热启动、应用附加播放延迟与冻结。
- 中文/英文直播各至少连续 30 分钟，后 5 分钟延迟中位数相对前 5 分钟增长 ≤1 秒。来源固有延迟没有可信基准时写未知。
- 至少 30 分钟本地媒体，识别未完成时即可播放，准备/暂停/跳转/取消、资源峰值和释放均观察。固定本地准备时间至少重复 3 次。
- 桌面最多两行，无裁切、全屏可见；因积压调度导致的连续不足 0.6 秒换页为零。不要把过期原文延长到后续无关语音。
- 隐式翻译请求为零；完成后暂停/跳转新增识别为零；停止或换会话后旧字幕污染为零。
- 同次直播字幕故障累计最多等待 2 秒；本地连续 15 秒无真实进展停止新投送并提供重试、仅播放、停止。临时故障总计最多恢复 2 次，初始退避 1 秒/2 秒。

以上是当前工程验收目标，不是已经测得的产品性能。完整定义和 A01–A10 仍以 PRD 为准。

### 7.2 已有真实样本

`uploads/output.mp4`：30.12 秒，4,140,367 字节，本次文件 SHA-256：

```text
de561b652a2812620a03cf7c93affb59bcbaa1bf31d4e9676203a5c9674f0cf0
```

10 月 22 日真实原文识别记录约 8.078 秒完成；实际播放器 5.1204 秒显示“别太装”，23.7948 秒显示“我们还是很期待的”。这是两处画面核对，不代表全片准确率/覆盖/流式延迟达标，也不能把 8 秒当所有文件的承诺。英文另有 10.2 秒固定样本，约 1.781 秒识别完成、4 段终稿、无翻译事件；当时主要证明识别链路。

仓库内证据在 [logs/original-caption-acceptance/](../../logs/original-caption-acceptance)：`output-original.json`、`english-original.json`、`browser-proof.json`、`output-at-5s.png`、`output-at-23s.png`、`output.display-cues.json`、`output.original.srt`。这些文件被 Git 忽略，但当前共用磁盘上存在。

### 7.3 长直播与安装试验

[logs/release-acceptance/](../../logs/release-acceptance) 保留来源探测、`only-play.json/png`、直播观察、失败和隔离安装记录：

- 英文 CGTN 历史直接流 `https://english-livebkali.cgtn.com/live/encgtn.m3u8` 曾连续观察约 30 分 50 秒；`en-live-long.json` 最后状态仍为“字幕暂不可用，部分缺失；媒体继续播放，完整结果见记录”。它证明长播放局部行为，**不是完整 A04 或延迟/覆盖通过**。
- 河北历史源 `https://event.pull.hebtv.com/jishi/cp1.m3u8` 曾可解码，但画面是 TVU 测试卡、无语音；不能冒充中文 ASR 验收。
- 华声历史源 `https://liveplay-srs.voc.com.cn/hls/tv/134_180adf.m3u8` 曾有中文，但播放/字幕时钟停滞，留下 `zh-live-failure.log`；属于失败。
- `isolated-install.json` 的依赖和导入退出码为 0，输出 `CLOUD_IMPORTS_OK`。只证明当时同机隔离 Python 环境可安装/导入，**不是干净 Windows 首装旅程**。

上述 URL 只是复现线索，本次没有重新探测，不承诺现时有效。私人来源参数和会话凭证不可直接写进公开矩阵。

### 7.4 FLEURS 参考集与旧录音问题

仓库外相对目录 [../../codex_tmp/2026-10-22/stream-release/fleurs/](../../../../codex_tmp/2026-10-22/stream-release/fleurs) 当前存在：中文 `cmn_hans_cn` 600.54 秒、英文 `en_us` 476.74 秒，各取 validation 固定前 50 项，附 manifest 和合并 WAV。manifest 标注来源 `google/fleurs`、CC-BY-4.0，使用时保留归属并人工核对参考。

- 中文合并音频 manifest SHA：`57768dc4f60d35c7193da61bf376dfda5749466baf76f20c0c23c3776f824004`。
- 英文合并音频 manifest SHA：`6b06e9971a9105db8265cbeaadfbfae6b81b140f559db5199de6a9860ebbbb8b`。
- 再次使用前实际计算音频哈希，不能只相信 manifest。其中路径字段是旧绝对路径，按当前挂载点解析；不要变更参考文本或归一化规则来提高成绩。
- 原始下载可能是 float WAV，历史准备过程用 PyAV 转 16kHz 单声道 PCM。下载 API 的音频签名 URL 会过期，已有文件优先复用。

旧 `release-fleurs-zh.json`、`release-fleurs-zh-paced.json`、`release-fleurs-en-paced.json` 还在原文证据目录。最初快速投送曾导致 `NO_INPUT_AUDIO_ERROR`，后来进行了节奏修复；**不能仅凭 paced 文件无 errors 且有 end 就认为 FLEURS 达标**。本次读取时两个 paced 文件的 events 都混有指向 `uploads/output.mp4` 的会话日志；加上旧格式缺少新的输入来源证明，必须重新核对身份/完整性，不能宣称已验证相同参考音频。

当前评分工具已区分 `source_verified/source_status`、`transcription_accuracy_passed`、`sync_gate_passed`、`release_gate_passed`。旧录音可计算数值，但不能自动升级为可采信的来源证据；`accuracy_gate_passed` 不隐含 `source_verified=true`。CLI 生成 JSON 或退出 0 也不表示全部门槛通过，必须读报告字段。

### 7.5 最近离线真实播放器证据

[logs/original-release-browser/report.json](../../logs/original-release-browser/report.json) 的时间为 `2026-10-23T12:29:14.969Z`，明确标注 `simulated_recognition=true`、`real_accuracy_tested=false`。本次可读取的报告只有 3 项：

- completed-local-control：完成后暂停/跳转，新增识别请求 0。
- withdrawal-stale-final：撤回后旧终稿不复现。
- narrow-long-caption：320px 窄屏下短句两行、30px、无水平裁切。

报告 `page_errors=[]`。目录有 `completed-local.png`、`narrow-caption.png`；**没有本次可确认的 64px 字号/全屏通过记录或 fullscreen 截图**。HEAD 已增加这些断言，但应在 Ubuntu 重新执行，再更新证据与任务状态。

### 7.6 自动化记录不是当前发布结论

- `9edc43f` 历史记录：127 项 Python、22 项 Node 通过。
- `98b798b` 的 Linux 接续记录：136 项 Python 通过、1 项 Windows DPAPI 测试跳过，31 项 Node 通过，CI 范围 Pylint errors-only 通过。Linux 跳过不能视为 DPAPI 通过。
- 后续 01/02/07 又有代码和测试增量；本次交接**没有重跑全套**，不把上述旧数字标在 `714f9d8` 上。

## 8. Ubuntu 双系统接续方法

### 8.1 打开同一份文件，不重新迁移

Windows 位置是 `D:\MyProject\stream-with-asr`。在 Ubuntu 找到该卷的实际挂载位置，再进入 `MyProject/stream-with-asr`。现有 `.scratch/linux-venv/pyvenv.cfg` 留下过 `/media/wbb/系统/MyProject/stream-with-asr` 的历史路径，但这不是对本次挂载点的保证；原生 Ubuntu 不要直接套用 WSL 的 `/mnt/d`。

进入后先执行：

```bash
pwd -P
git status -sb
git rev-parse HEAD
git branch -vv
git worktree list --porcelain
```

确认本文件和 `714f9d8` 所在分支可见。共享文件可读不代表 Windows 进程、浏览器标签、环境变量和内存 Key 会随重启保留。

如果 Git 报所有权或挂载权限问题，先核实路径和卷权限，只对已确认的仓库作必要配置；不要全局关闭所有仓库安全检查。不要直接运行 `git clean -xfd`、`git worktree prune` 或重置分支。

### 8.2 Python 环境

支持 Python **3.10–3.12**，推荐 3.12；项目声明 `<3.13`。Node CI 使用 22；历史 Linux 本地用过 Node 24.22.1。

- `.venv/Scripts/` 是 Windows 环境，保留给 Windows，Ubuntu 不能复用。
- `.scratch/linux-venv` 是已有 Linux 环境，但 pyvenv 中解释器指向 `/home/wbb/.cache/codex-runtimes/.../python3.12`；先检查能否运行，不因目录存在就认定可用。
- 共用 NTFS 卷可能影响符号链接和执行权限。需要新环境时，放在 Ubuntu 自己的 Linux 文件系统上；源码和媒体仍留在共用磁盘，不必复制仓库。

已在仓库根目录且系统有 Python 3.12 的情况下，新的独立开发环境示例：

```bash
python3.12 -m venv "$HOME/.venvs/stream-with-asr"
VENV="$HOME/.venvs/stream-with-asr"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r requirements-webui.txt
```

若缺少 venv 支持，安装与所选 Python 对应的系统包后再建环境；不要使用系统 `sudo pip`。这里只给接续命令，本次没有在 Ubuntu 执行安装。

`requirements-webui.txt` 是云端界面的轻依赖入口，含 PyAV、DashScope、NumPy、httpx、FastAPI、multipart、uvicorn、yt-dlp。使用兼容 wheel 时无需本地 GPU 模型或单独系统 FFmpeg；编译安装、其他工具链另算。依赖使用下限约束、未提供锁定环境，首次运行保留版本信息，不能假定与旧系统版本完全一致。

开发回归按 CI 安装完整项目与检查工具：

```bash
"$VENV/bin/python" -m pip install -e . pytest pylint playwright
"$VENV/bin/python" -m pip check
```

### 8.3 启动正常 WebUI

```bash
ss -ltnp 'sport = :8002'
"$VENV/bin/python" tools/webui.py --host 127.0.0.1 --port 8002
```

先检查端口所属，再判断是否需停止旧实例。浏览器打开 `http://127.0.0.1:8002/`，另一个终端可检查：

```bash
curl --fail http://127.0.0.1:8002/ -o /dev/null
curl --fail http://127.0.0.1:8002/api/cloud
```

同机 Ubuntu 本地浏览器无需 SSH 转发。`webui.py` 不传 host 时默认监听 `0.0.0.0`；普通本机开发显式 loopback。需要复核已有局域网分享时才按其边界配置监听/私网防火墙，管理页仍只允许本机，不要为了从别处打开而删除访问检查。

WebUI 代码修改后，按 AGENTS 要求重启同一个 8002 并验证 HTTP，不能仅刷新静态页面就声称后端已更新。本文件没有记录当前运行 PID，也没有在交接中重启服务；历史 PID/端口运行状态不可继承。

### 8.4 Key、持久化与旧环境

- Ubuntu 在页面配置 Key，默认仅当前服务进程内存有效。不要将旧聊天中的 Key 抄到本文件、源码、命令行、报告或 Git。
- 选填持久保存使用 Windows 用户绑定 DPAPI，文件是 `.data/cloud.bin`；Linux 不支持该持久化，不能改成明文降级。双系统能读取文件也不能解密 Windows DPAPI。若旧保存文件阻止加载，按界面删除配置后重新填入；本次 `.data` 目录为空。
- 当前页面的 CloudConfigStore 不会仅因存在 `DASHSCOPE_API_KEY` 环境变量就自动显示已配置。传统引擎仍有环境变量路径，`webui.main()` 还兼容读取 `~/Projects/.env` 中的 `dashscopetoken`；这是历史开发便利，不应依赖其作为新用户配置旅程。
- 先跑无付费替身测试；真实云端验收需要当前可用配置及明确样本/调用上限。历史授权和旧 Key 不等于允许无限重跑。

### 8.5 双系统 worktree 的特殊风险

同一 `.git` 保存了两套操作系统的绝对 worktree 路径。Windows 当前将以下 Linux 路径报告为 prunable，但不代表 Ubuntu 上一定没有文件：

```text
/tmp/stream-asr-impl-20261023/input
/tmp/stream-asr-impl-20261023/display
/tmp/stream-asr-impl-20261023/evidence
/tmp/stream-asr-impl-20261023/preparation
/tmp/stream-asr-impl-20261023/controls
```

这些分支对应 `impl/caption-01-input`、`02-display`、`07-evidence`、`03-preparation`、`04-controls`。任务 07 还引用 `/tmp/stream-asr-impl-20261023/notes/evidence.md`。它们在 Ubuntu 根文件系统，不能用 Windows 找不到路径就判定已删除；反过来 `/tmp` 也可能重启清理，接手后先查是否仍存在、是否有未提交修改。不要先 prune 或重新创建覆盖。

另有旧 Windows `codex/optimize-*` 与 `codex/refine-webui-20261020` 工作区，路径位于仓库外 `../../codex_tmp/`。它们主要是历史实验/已合入成果，不是当前开发入口。跨系统修复 worktree 元数据前先保存需要的修改，必要时使用 Git 的 worktree repair 并核对每条实际路径；不要手工批量改 `.git` 指针或误删未合并分支。

## 9. 测试和证据工具怎样使用

### 9.1 常规回归

从根目录执行：

```bash
"$VENV/bin/python" -m pytest test/ -q -o cache_dir=.scratch/pytest-cache
node --test tests/frontend/caption_browser.test.cjs
git diff --check
```

CI 的 [test.yml](../../.github/workflows/test.yml) 在 Ubuntu 跑 Python 3.10/3.11/3.12 和 Node 22。普通 pytest/Node 与独立浏览器/真实云端脚本分开；安装 Playwright 包并不意味着浏览器 E2E 已执行。

静态检查以 [pylint.yml](../../.github/workflows/pylint.yml) 为准，核心参数：

```text
--errors-only
--init-hook="import sys; sys.path.insert(0,'whisper_streaming'); sys.path.insert(0,'tools')"
--disable=E0611,E1101,E0110
```

对 Git 跟踪的 Python 文件运行，排除 vendored `whisper_streaming/`。不要把缺可选本地模型包或上游 vendored 文件的全量 lint 报错混作本轮代码新增问题。

### 9.2 无付费真实浏览器检查

入口是 [original_release_fixture.py](../../tests/acceptance/original_release_fixture.py) 与 [e2e_original_release.py](../../tests/browser/e2e_original_release.py)。使用真实页面、路由、解码和播放器，以生成媒体及预设 ASR 事件替代外部识别；不是实际语音准确率测试。

操作顺序：

1. 检查并停止正常 8002 服务，记录恢复方式。
2. 一个终端运行 `"$VENV/bin/python" tests/acceptance/original_release_fixture.py`，只绑定 `127.0.0.1:8002`。
3. 另一个终端运行 `"$VENV/bin/python" tests/browser/e2e_original_release.py`。
4. 核对 `logs/original-release-browser/report.json` 的实际案例、截图和退出结果，保留失败。
5. 停止 fixture，恢复正常 WebUI，确认 `/` 与 `/api/cloud` 响应。

**当前可移植性缺口：** E2E 脚本硬编码 Chrome 路径 `/usr/bin/google-chrome`。Ubuntu 没有该浏览器时，安装 Playwright Chromium 不会自动改变此路径；需先确认浏览器和 H.264/AAC 解码能力，再调整为明确可用的执行路径或浏览器配置。这是接手时应处理的小项，本文没有暗中修改脚本。

fixture 有 `normal/long/silent/withdrawn` 场景，但当前 E2E 不覆盖所有任务要求；尤其不能声称已覆盖 30 分钟、所有故障、连续跳转、第二设备或 Windows。

### 9.3 真实原文录音和评分

[run_original_acceptance.py](../../tests/acceptance/run_original_acceptance.py) 经正常 WebUI 的公开 HTTP/WS 接口录制，**会调用已配置的真实识别服务**；不能把以下命令当免费冒烟自动执行。确认有限样本后，命令形式为：

```bash
"$VENV/bin/python" tests/acceptance/run_original_acceptance.py uploads/output.mp4 \
  --label user-video --output logs/original-caption-acceptance/new-output-original.json
```

该工具接受本地文件和 HTTP(S) 来源，记录本地输入前后 SHA/身份、正常完成/取消和错误；远端记录来源与采样区间，URL 不是不可变内容哈希。需要超时或人工停止的直播应保留取消/失败结果，不伪装成正常完成。

有匹配参考 manifest 后，评分形式为：

```bash
"$VENV/bin/python" tests/acceptance/score_original_acceptance.py path/to/manifest.json \
  path/to/recording.json --language zh --output logs/score-zh.json
```

`path/to/...` 为占位，不是已存在文件。英文用 `--language en`。归一化固定为 NFKC、小写、移除标点等脚本规则，数字写法保持预定规则；修订/撤回不重复计数。分别检查准确率、来源证明和同步/发布字段，不能把评分当画面验收。

## 10. 输入来源、研究路线与避免重复踩坑

来源至少分三种：

- **频道列表**：M3U/M3U8 中每个 EXTINF 是一个频道及下游地址。
- **HLS 媒体清单**：EXTINF 是媒体分片，不能解析成一堆 `no desc` 频道。
- **平台页面**：先经 yt-dlp 解析，解析成功后仍要验证拉流、解码、浏览器画面、语音及字幕。

历史频道重开 bug 来自选台后把输入框替换为媒体 URL，又把其分片当频道；已做保留原频道清单和类别检查。新 UI 变动不得重新引入。黑屏但有声音会继续触发后端 ASR，所以无音轨、不可解码/已知不支持格式应尽量在创建识别器前拒绝，播放故障后的停止仍需验证。

候选发现入口（非当前可播承诺）：

- `https://iptv-org.github.io/iptv/languages/zho.m3u`
- `https://iptv-org.github.io/iptv/languages/eng.m3u`
- `https://raw.githubusercontent.com/best-fan/iptv-sources/main/cn_cctv.m3u8`
- `https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8`
- B 站春晚页 `https://www.bilibili.com/bangumi/play/ss79829/`，仅作平台页候选，可能受地区/账号限制。

Guovin 旧 raw `.../output/...` 地址在后续调研中已标明不再更新，不能复制早期推荐称为有效。用户希望提供实际可用地址和观看结果，不是只贴 GitHub 仓库。每个案例绑定输入、版本、时间、环境、输出和判定，失败不能拿另一个来源的成功替代。

早期曾调查 Qwen 相关现成项目：`QwenLM/Qwen3-ASR`、`LanceLRQ/qwen3-asr-service`、`liuniang3/open-voice-input`、`caitunai/asr`。历史结论是它们可参考 ASR 服务/协议，不能视为已替代本项目媒体和字幕全链路；当前真正接入仍是 FunASR。不要仅替换模型字符串就声称接入 Qwen，不要将本地 Qwen、云端 Realtime 和 FunASR 协议混同。未来恢复翻译或切换模型需重新核实官方文档及时间戳能力，另立需求/验收。

UI 美化曾安装并应用 `frontend-design`（anthropics/skills）和 `ui-ux-pro-max`（nextlevelbuilder/ui-ux-pro-max-skill），安装位置在仓库外。Ubuntu 的 Codex 不会因共享项目文件自动继承 Windows 插件/skills；已有 UI 代码可直接继续维护，无需为接手先安装一套插件。设计意图见 `docs/guides/ui-design.md`；其中 2.4–8 秒/14 秒阅读队列属于历史翻译路径，不可搬到原文时轴。

## 11. 本地资料、Git 与 tracker

共用磁盘上的以下目录被 Git 忽略，Ubuntu 可以直接访问，并非这次需要另行迁移：

- `uploads/`：用户 `output.mp4` 和早期示例媒体。
- `logs/original-caption-acceptance/`：原文事件、SRT、画面与旧 FLEURS 录音。
- `logs/original-release-browser/`、`logs/original-release-fixture/`：模拟识别的真实浏览器报告、截图和生成媒体。
- `logs/release-acceptance/`：直播、仅播放、来源失败、隔离安装、参考集准备脚本。
- `logs/translation-acceptance/`：历史互译样本、暂停恢复与 UI 截图，仅作历史。
- `../../codex_tmp/2026-10-22/stream-release/`：FLEURS 音频/manifest、隔离安装等。
- `../../codex_tmp/2026-10-17/amd-asr-ollama/`：历史 Windows 本地 Vulkan 资料，不要求当前云端产品使用。

“全部文件可访问”不等于路径字符串自动变成 Linux 路径。旧 JSON、脚本可能含 `D:/...`、`C:/...` 或过期下载 URL，先按实际输入和哈希映射，不把找不到旧绝对路径误判为媒体丢失。原始运行日志可能含敏感源参数/历史会话信息，不原样上传或批量纳入 Git。

远程仓库为 `https://github.com/AiKiAi-stack/stream-with-asr.git`。历史清理完成过一批 PR 合并和分支删除，但那是当时快照。本次仅核对本地 refs，当前没有推送或创建 PR；同一份磁盘继续开发无需依靠远端克隆。

长期 tracker 是 GitHub Issues，默认标签 `needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。10 月 23 日因用户暂时无法登录 GitHub，先把已确认任务保存在本地；这是获准的临时方式，不用为登录暂停所有开发。恢复登录后先查询所有状态的已有 Issue 去重，再迁移 01–12、回填链接；不要把 L01–L06 也重复建为另一套执行任务。

代理模型约定现在是主任务 `gpt-6.1-sol/max`、子代理 `gpt-6-luna/max`，保存在 `AGENTS.md` 与 `.codex/config.toml`。旧聊天出现过 Astra/Max、Sol/High，均不是当前项目默认。是否实际启用仍由 Ubuntu 宿主支持和当前会话设置决定，文件不能自动切换正在运行的模型。

## 12. 已汇总的项目会话

以下为本次应用内可读取的项目会话，标题按原样保留，ID 用于追溯；Ubuntu 无需恢复这些会话才能按本文继续。

- **[26-10-21] 项目产品定位与PRD规划** — `01a15e7a-0e0e-7c91-ba7f-8507dbd45c2a`。公开用户定位、Windows/云端 Key/两类输入、互译首版及 UI 修复、原文模式转向、PRD v1.0、发布验收与本次上下文交接。最新用户纠正：同机双系统，共用文件；只需交接上下文。
- **[26-10-18] 直播字幕同步修复** — `01a1480c-b3f9-7e41-ad8b-e512e9ccb8d4`。Qwen 项目研究、demo 引擎、本地 Vulkan 文件服务、FunASR 音频尾帧与错误、真实 HLS/时间轴/稳定短句、长句与单词拼接、频道重开、PR #26、最终统一 8002。多轮短测成功后仍暴露持续体验问题，是必须真实浏览器验收的主要教训。
- **[26-10-18] 跨设备观看与转发字幕直播** — `01a14ade-1c22-77a2-ba63-696fd3406e9e`。局域网随机只读链接、最多 3 观众、唯一识别、当前直播位置、停止失效、管理仅本机；要求 spec→ticket→implement，以公开 HTTP/WS 黑盒边界验收，形成 #27–#30。
- **[26-10-18] stream-with-asr 优化与 DAG 执行** — `01a14f46-8815-7ef2-9677-426df7f3037f`。断开名额与平台解析阻塞的可复现修复，#31/#32、PR #33、95 项历史回归；用户改为普通提交/PR 流程，不在产品 PR 放 DAG 文件。内存紧张时不可擅停其他项目服务。
- **[26-10-18] GitHub分支与PR清理** — `01a14ad5-2822-75d2-9e1d-0f908355adde`。当时完成 gh 登录、7 个 PR 合并和已合并分支清理，本地 main 对齐；现在不能把“当时无开放 PR”当当前事实，不能继承旧登录凭据到 Ubuntu。
- **[26-10-18] 查找可用中文 M3U 直播源** — `01a14a8e-5716-7890-80b3-89a4437a7b4f`。用户要直接 M3U 链接和春晚测试资源；早期列表推荐多未实际播放，后续来源调研及实测结果优先。
- **[26-10-18] Clone 仓库并安装全局 Skill** — `01a14f2d-5e82-7ea2-af6a-7cf41fdeaae6`。主要是另一个 dag-skill 仓库/全局技能及模型设置。曾误改仓库后撤回；对本项目有用的是先确认实际 repo、同步 AGENTS 与配置，历史模型值已被当前默认覆盖。
- **[26-10-17] 本机可部署的 LLM 项目发现** — `01a1489a-d369-7cb1-a565-bb1084cb121a`。虽归在同一项目分组，主体是其他本地 LLM 项目发现，不计为本字幕产品的已实现功能或待办。

可读取的已归档会话列表中没有发现额外的本项目会话。与 ASR/AMD 的项目外研究只保留当前仓库有关线索，不把其他项目的部署实验并入发布验收。

## 13. 下一次 Ubuntu 会话的具体起步

1. 打开共用磁盘的同一个仓库，读本文件、AGENTS、PRD、ADR-0008、规格。核对 HEAD、工作树与 `/tmp` 工作区，保留已有修改。
2. 选择能运行的 Linux Python 环境，记录依赖版本，检查唯一 8002。不要复制 Windows `.venv`。
3. 对集成版本跑常规回归，解决 E2E 浏览器路径问题，重跑新增 64px/全屏场景；对实际报告逐项核对，而不是从脚本内容推断通过。
4. 核对 01/02/07 的代码与证据，更新任务和实施状态中的过期描述；保留真正未通过的项。
5. 按 03/04、05/06 补行为和故障证据，再判断 09/10 的依赖是否满足。参考集和来源准备可以提前，但真实云端调用需有明确边界。
6. 08/11 按用户决定保持延后，12 保持未通过；不自行扩大范围、删门槛或宣告公开发布。

可以把下面这段直接交给 Ubuntu Codex：

> 这是 Windows/Ubuntu 双系统共用的 stream-with-asr 仓库。请先阅读根目录 handoff.md、AGENTS.md、当前 PRD、ADR-0008 及 2026-10-23 实现规格，再核对实际 Git 与工作区。继续 feat/original-caption-release 的原文字幕首版工作；先核对已集成代码、任务状态和无付费真实浏览器证据，修复 64px/全屏等尚未闭环项，再按已确认依赖推进。共用文件可直接访问，路径按实际挂载点处理，不能覆写 Windows 环境或清理未确认 worktree。保留唯一 8002、用户已延后的 08/11，以及尚未通过的发布门槛；不要自动恢复翻译或无限调用付费 API。

本次交接只新增本文件；没有修改产品代码、关闭任务、迁移磁盘、创建迁移包、提交/推送、重启服务或重新执行付费验收。后续请按实际结果更新本文，避免再次把历史快照当当前状态。


## 2026-10-24 接续交接

当前集成分支 `feat/original-caption-release`，代码提交 `fe0ff14`。01–07实现及离线行为回归完成，188项Python通过/1项Windows跳过，90项Node通过；真实浏览器覆盖恢复、共享历史截断及旧媒体回调。所有这些识别均为模拟，不能替代真实语言质量。

当前状态以 `docs/plans/2026-10-23-original-caption-implementation-status.md`、`docs/plans/2026-10-24-original-caption-code-review.md`、`docs/plans/2026-10-24-original-caption-release-evidence.md` 和正式12项本地Issue为准。两语五分钟单说话人媒体及一分钟候选已在项目 `uploads/acceptance-media/`，文本/哈希/许可证见研究清单。09/10还缺本轮真实配置、独立听审和质量/直播长测；08/11用户延期，12未通过，暂不开下一份TR/spec。

普通页面已恢复到 `http://127.0.0.1:8002/`，页面和资源健康；当前没有识别配置、没有真实云调用。GitHub已登录AiKiAi-stack，本轮只有本地提交和任务记录，没有推送或在线Issue/PR。临时实现工作区已清理，分支和日志保留。
