# 验收媒体清单与来源核验（2026-10-24）

本记录整理当前可复用的本地语音、直播候选来源及有界连通性探测。它用于准备后续验收，不代表识别质量、字幕同步、浏览器播放或直播稳定性通过。网页/清单上的频道标签没有代替人工试听确认实际语音语言。

## 已有 FLEURS 参考音频

素材已经存在，不需要重复下载：`../../codex_tmp/2026-10-22/stream-release/fleurs/`（从仓库目录相对定位）。清单里的旧 Windows 根路径 `D:\codex_tmp\...` 映射到本机 `/media/wbb/系统/codex_tmp/...`；对应目录的每个条目均已在此映射下找到。

| 语言 / 配置 | 清单与合并音频 | 条目 | 合并音频实测 | 清单 SHA-256 与本地实测 |
|---|---|---:|---|---|
| 中文简体 `cmn_hans_cn` | `cmn_hans_cn-manifest.json`、`cmn_hans_cn.wav` | 50/50 条目存在且逐条哈希一致 | 600.54 秒，单声道、16 kHz、16-bit PCM，9,608,640 帧 | `57768dc4f60d35c7193da61bf376dfda5749466baf76f20c0c23c3776f824004`，一致 |
| 美式英语 `en_us` | `en_us-manifest.json`、`en_us.wav` | 50/50 条目存在且逐条哈希一致 | 476.74 秒，单声道、16 kHz、16-bit PCM，7,627,840 帧 | `6b06e9971a9105db8265cbeaadfbfae6b81b140f559db5199de6a9860ebbbb8b`，一致 |

清单记录数据集为 [`google/fleurs`](https://huggingface.co/datasets/google/fleurs)，`validation` split，固定取前 50 行，并保存原始 row、dataset ID、人工参考文本、逐条 WAV SHA-256 及起止时间。每条之间有 0.5 秒静音，因此中文实际条目音频合计 575.54 秒、英文 451.74 秒，合并文件分别另含 25 秒填充静音。静音不是语音参考时长。

当前清单足以复用已有固定输入并复核文件身份。若要在另一台机器重建，使用 Hugging Face `datasets` 按 `google/fleurs` 的 `cmn_hans_cn` 或 `en_us` 配置取得 `validation` split，只取记录的 row/ID，并逐项比对哈希；必须先固定数据集仓库 commit/revision，因为 `main` 可变。官方入口和加载说明见 [FLEURS 数据卡](https://huggingface.co/datasets/google/fleurs)、[Datasets quickstart](https://huggingface.co/docs/datasets/quickstart)。不要加载 `all` 配置来重建这两份小样本。当前机器已有经转换的文件，不应再次下载整套数据。

数据卡将 FLEURS 标为 CC BY 4.0。若复制或再发布音频/派生样本，应保留来源归属、数据卡与许可链接，并说明转换；例如：“FLEURS（Google），`cmn_hans_cn` / `en_us` validation 记录；CC BY 4.0；已转为单声道 16 kHz 16-bit PCM，并在每条后加入 0.5 秒静音。”具体义务以 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 为准。

**适用边界：**FLEURS 是朗读语音数据，不是连续直播/访谈录音。现有 manifest 没有记录说话人身份或语速，也没有人工确认单一说话人、连续讲话或快语速。虽然合并 WAV 时段长度已超过 5 分钟，但不能据此宣称满足 PRD 中“中、英文各至少 5 分钟清晰单人独立参考”和“各至少 1 分钟快语速”的质量验收；应另准备连续说话人素材，人工核对边界、说话人、语速与参考稿后绑定 SHA。

## 直播来源与许可边界

[`iptv-org/iptv`](https://github.com/iptv-org/iptv) 提供由用户提交的公开 IPTV 地址清单，维护者称播放列表每日 00:00 UTC 生成；本次读取其 [中文语言列表](https://iptv-org.github.io/iptv/languages/zho.m3u)、[英文语言列表](https://iptv-org.github.io/iptv/languages/eng.m3u) 和 [中国频道列表](https://iptv-org.github.io/iptv/countries/cn.m3u)，HTTP 均为 200。中文、英文列表当次分别有 219、3,004 条目；中国列表当次响应约 30 KB。列表可能更新，具体播放地址、节目、实际语音、地区限制和稳定性都可能变化。

`iptv-org` 仓库的 [LICENSE](https://github.com/iptv-org/iptv/blob/master/LICENSE) 是 Unlicense，但这只说明仓库中清单/代码的许可，不是清单所指广播内容的播放或再分发授权。其 [README 法律说明](https://github.com/iptv-org/iptv/blob/master/README.md) 也说明仓库不托管视频文件且不控制目的地。探测前仍须按频道方条款及测试所在地规则判断可否使用；勿把可取得的 URL 当成内容授权。

官方频道页面可用于确认候选身份，不足以证明当前媒体端点可用：CCTV 有 [CCTV-13 直播页](https://tv.cctv.com/live/cctv13/index.shtml) 和 [CCTV-4 直播页](https://tv.cctv.com/live/cctv4/index.shtml)；DW 有 [英文直播页](https://www.dw.com/en/live-tv/channel-english)。本次没有从 CCTV 页面确认出可直接复现的单路媒体 URL。DW 页面 HTML 包含一个 HLS 播放端点，以下只记域名并做了短解码。

## 有界探测结果

探测日期为 2026-10-24。每个来源最多取一次 HLS 清单/首段并尝试解码一帧，单源耗时均低于 15 秒；共六个候选。媒体只经代理感知的 HTTP 获取后交给本机 PyAV/FFmpeg 解码，没有调用 ASR 服务、使用 API Key 或保存完整直播。主机/频道只用于定位候选，不应视为持续有效地址。

| # | 候选及发现路径 | 当前探测结果 | 能说明什么 |
|---:|---|---|---|
| 1 | 中文列表中的 CCTV-4 America 候选；`global.cgtn.cicc.media.caton.cloud` | 取流失败（`URLError`，约 0.65 秒） | 本次网络路径未取得可解码媒体；不能判定频道长期不可用 |
| 2 | 中文列表中的 VOA TV China 候选；[HLS](https://voa-ingest.akamaized.net/hls/live/2033878/tvmc08/playlist.m3u8) | 清单 HTTP 200；首段 HTTP 200；AAC 音频解出 1 帧（1024 samples，48 kHz），约 4.14 秒 | 证明短时 HTTP/HLS 与音频解码可行；未确认实际语言或字幕 |
| 3 | 中文列表中的 CCTV+1（列表标为 Not 24/7）；`cd-live-stream.news.cctvplus.com` | 清单及 master HTTP 200；后续片段 HTTP 请求失败，约 1.01 秒 | 不适合作为当前稳定 30 分钟候选；清单标签也提示非全天候 |
| 4 | DW 官方英文直播页嵌入的 [HLS](https://dwamdstream102.akamaized.net/hls/live/2015525/dwstream102/master.m3u8) | 清单、master、首段 HTTP 200；AAC 音频解出 1 帧（1024 samples，48 kHz），约 2.54 秒 | 证明页面当前提供的候选端点可短时解码；不证明听感语言、播放许可或长时稳定 |
| 5 | 英文列表中的 Al Jazeera English；[HLS](https://live-hls-apps-aje-fa.getaj.net/AJE/index.m3u8) | 清单、master、首段 HTTP 200；AAC 音频解出 1 帧（1024 samples，48 kHz），约 5.05 秒 | 仅为列表候选的短时传输/解码结果 |
| 6 | 英文列表中的 CGTN；[HLS](https://amg00405-rakutentv-cgtn-rakuten-i9tar.amagi.tv/master.m3u8) | 清单、master、首段 HTTP 200；AAC 音频解出 1 帧（1024 samples，48 kHz），约 2.69 秒 | 仅为列表候选的短时传输/解码结果 |

四个成功候选的 URL 和当日对应来源快照信息均留在本机 [`source-candidates-2026-10-24.json`](../../logs/release-acceptance/source-candidates-2026-10-24.json)。其中 IPTV 清单保存了 SHA-256 和目标频道的 `EXTINF` 条目摘录；因此可对应到获取时的清单版本，同时避免在文档中复制整份列表。

列表条目和域名不能证明频道权利、真实口语语言、后续是否静音/广告、浏览器能否播放、ASR 质量、字幕同步、30 分钟持续可用或延迟增长。没有试听音频，也没有进行付费识别。来源固有缓冲/时钟未测。本次结果是当天、当前网络和短时请求下的连通性快照，不能替代 PRD 直播验收。

## 后续可执行准备

1. 直接复用上述两组 FLEURS 合并 WAV 和 manifest 做固定输入回归；在评分前冻结归一化规则，保证输入 SHA 与参考稿一起记录。它们可以检查结果可复现性，不能代替单人连续语音与快语速门槛。
2. 为中、英文各准备至少 5 分钟连续、清晰、可授权且单说话人的本地参考，并各准备至少 1 分钟快语速片段；人工确认音频、真实语言、分段边界及人工参考稿，记录 SHA-256、精确时长、音轨参数和许可/来源。公开频道录音应先核实内容使用权。
3. 直播验收候选先试听确认实际口语，再记录频道/地区、测试网络、当时解析出的媒体 URL（私有复现记录可保存在本机，公开报告需脱敏）、采样时间和失效后的失败证据。只有通过真实播放器观察并完整达到 PRD 时长/指标，才能作为直播验收证据。
4. 对要重复使用的在线来源固定当日 playlist 快照或 commit、配置和候选条目；重跑时重新确认访问权限、HTTP、浏览器播放及试听语言，不把本报告中的一次短解码当成稳定基线。
