# 媒体来源基线调研（2026-10-21）

## 范围与边界

按产品输入筛选：公开用户、Windows 本机浏览器、仅用户自带云端 API Key、直播链接与本地视频、中译英/英译中优先、当前句可修订、低延迟为目标。查询日期为 2026-10-21（Asia/Shanghai）。仅查看官方仓库、文档、播放列表文本和平台页元数据；没有下载媒体或播放/测试直播。因此下文给出候选与来源自述，不承诺目前可播。语言/频道标签和标题也不能代替试听确认实际口语。

## 输入类型

- **播放列表**：M3U/M3U8 文本含频道元数据及下游地址；扩展名以 .m3u8 结尾也可能是频道索引，不是单路媒体流。示例：[best-fan 央视列表](https://raw.githubusercontent.com/best-fan/iptv-sources/main/cn_cctv.m3u8)、[ChinaIPTV 自动列表](https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8)。
- **直接媒体流**：列表中的具体 HLS 等播放地址，可能离线、限地区、要求请求头或含会变化的签名参数。清单里有地址不代表当前能播放。
- **平台页**：YouTube/B站网页 URL。项目已有 [platform_resolver.py](../../lisca/platform_resolver.py) 用 yt-dlp 解析平台页。yt-dlp 官方支持表列有 YouTube、BiliBili、BiliBiliBangumi，但明确说明网站变动会使支持失效，列表不保证具体 URL 可用。[官方支持表](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

## 首批来源推荐

### 重复验收：先用本地视频

中、英各选一段自有或获准使用的本地视频，固定文件 SHA-256、片段起止位置、人工核对的语音语言和参考文本。相同文件与片段适合重复检查识别、翻译方向、当前句修订和延迟，避免网络、频道节目和平台解析变化干扰结果。播放列表语言标签或节目标题不作为口语语言证据。

### 直播冒烟：iptv-org 与 best-fan

[iptv-org/iptv 中国清单](https://iptv-org.github.io/iptv/countries/cn.m3u)、[中文清单](https://iptv-org.github.io/iptv/languages/zho.m3u)、[英文清单](https://iptv-org.github.io/iptv/languages/eng.m3u)适合筛选频道候选。仓库自述为公开频道集合；公开播放列表按频道数据库资料分组，每日 00:00 UTC 生成，并基于质量和标签选择可用版本中的较优地址。[README](https://github.com/iptv-org/iptv/blob/master/README.md) [生成规则](https://github.com/iptv-org/iptv/blob/master/docs/playlists.md) [播放列表目录](https://github.com/iptv-org/iptv/blob/master/PLAYLISTS.md)。中国清单中有标为 [Geo-blocked]、[Not 24/7] 的条目，实际口语仍须按验收网络试听确认。[当前清单文本](https://raw.githubusercontent.com/iptv-org/iptv/gh-pages/countries/cn.m3u)

[best-fan 央视清单](https://raw.githubusercontent.com/best-fan/iptv-sources/main/cn_cctv.m3u8)当前含 CCTV-4“中文国际”；文件头自报生成时间 2026-10-21 04:23:33，生成者为 Jenkins Auto Build。README 声称每日采集更新并检查有效性，也提醒源可能失效。这些是仓库维护声明与清单自报时间，不是播放验证。[README](https://github.com/best-fan/iptv-sources/blob/main/README.md) 适合作为中文直播冒烟的候选清单；从中选定具体频道后，要将实际媒体地址、选择时间、地区/网络、请求头需求和结果绑定到单独用例。

### 备选：ChinaIPTV 与 Guovin

[ChinaIPTV 自动列表](https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8)是 M3U 清单。其 README 声称每 15 分钟抓取更新；当前列表可见带查询参数的 HLS 地址。更新声明、参数有效期与播放均未独立验证。[README](https://github.com/hujingguang/ChinaIPTV/blob/main/README.md)

[Guovin/iptv-api 上游结果页](https://guovin.github.io/iptv-api/)列出 [IPv4 M3U](https://guovin.github.io/iptv-api/ipv4.m3u)。结果页当前标注生成时间 2026-10-12T11:25:51+08:00，并声明上游结果仅用于功能测试，不保证完整、可用或持续更新；仓库 README 也说旧 raw.githubusercontent.com/.../output/... 链接不再更新。因此不建议作为首批重复验收来源。[结果页声明](https://guovin.github.io/iptv-api/) [README](https://github.com/Guovin/iptv-api)

这类聚合仓库提供清单，不代表频道内容授权。best-fan 提醒遵守来源条款；Guovin 明确要求用户自行确认来源与使用行为合规。[best-fan](https://github.com/best-fan/iptv-sources/blob/main/README.md) [Guovin](https://github.com/Guovin/iptv-api)

### 平台页候选

[B站 2025 年央视春晚](https://www.bilibili.com/bangumi/play/ss79829/)页面显示全 1 集并提示部分集数受地区限制，可作较长、复杂现场声音的探索性用例，不能替代清晰短句基线。本次未核验音轨语言、账号/地区可访问性或 yt-dlp 解析结果。

[YouTube 春晚精选页](https://www.youtube.com/watch?v=kHKy19ozOJk)可作另一平台页候选；英文标题不证明音轨有英语口播。本次未播放或核验可访问性。平台页验收需记录 URL、测试时间、yt-dlp 版本、登录状态和地区/网络条件，并区分页面解析失败与媒体播放失败。

## 验收记录与项目约束

每条用例绑定一个输入、一个输出和一个人工判定：本地文件记哈希与片段；直播清单记清单 URL、所选频道及解析出的媒体地址；平台页记页面 URL 与解析器版本。实际输入语音需试听确认，再分别核对识别、翻译方向、当前句修订历史和终稿。来源失败时保留该用例失败证据，不以另一个来源的输出替代。

低延迟验收应记录音频输入到音视频与字幕显示的端到端测量及来源条件，再由产品给出数值目标；不要把来源缓冲或固定数秒当作承诺。现有 [ADR-0004](../adr/0004-buffered-audio-video-and-final-captions.md) 采用默认 5 秒最小整体延迟与稳定句/终稿推进播放，与本次产品输入“低延迟、当前句允许修订”需在产品/架构文档中统一；本来源调研不替代该决策。

## 一手来源

- [iptv-org README](https://github.com/iptv-org/iptv/blob/master/README.md)、[播放列表规则](https://github.com/iptv-org/iptv/blob/master/docs/playlists.md)、[列表目录](https://github.com/iptv-org/iptv/blob/master/PLAYLISTS.md)
- [Guovin/iptv-api](https://github.com/Guovin/iptv-api)、[上游结果页](https://guovin.github.io/iptv-api/)
- [best-fan README](https://github.com/best-fan/iptv-sources/blob/main/README.md)、[央视 M3U](https://raw.githubusercontent.com/best-fan/iptv-sources/main/cn_cctv.m3u8)
- [ChinaIPTV README](https://github.com/hujingguang/ChinaIPTV/blob/main/README.md)、[自动 M3U](https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8)
- [yt-dlp 支持站点与限制](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [B站节目页](https://www.bilibili.com/bangumi/play/ss79829/)、[YouTube 候选页](https://www.youtube.com/watch?v=kHKy19ozOJk)
