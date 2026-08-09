# SPEC — webUI 直播源观看（round5）

状态: 定案（grill 三轮完成 + 批量检测补充 + Q3 实测修正为 hls.js）→ 已实现（repro_live_iptv 4/4 + e2e 5/5 + pytest 49 + pylint 0）
来源: 用户 2026-08-09「接下来是真正的直播」；提供真实源 https://live.zbds.top/tv/iptv4.m3u（385 频道）；走 ask-matt 流程 grill → SPEC → ticket → implement

## 0. 架构事实（实现者必读）

- 后端 `tools/webui.py`：`GET /video?url=` / `GET /audio?url=`（httpx 代理：Referer/UA 补头 + verify=False + Range 透传；HLS 列表经 `_rewrite_hls_playlist` 改写后返回；LIVE 列表每次请求实时拉上游，无媒体缓存）；`WS /ws?url&engine&silence&win`（worker：StreamSource/PyAV 按实时速率 0.1s/chunk 推引擎 → emit 字幕 + ready 信号，直播延时模型已就绪）；`GET /` 单页 index.html（no-store）
- `_rewrite_hls_playlist(text, stream_url)`（webui.py ~行 128）：普通行 urljoin、`URI="..."` 属性改写（EXT-X-KEY/MEDIA/STREAM-INF，ticket-11）；**当前输出 = 上游绝对 URL** → 浏览器直连上游分片
- **实测根因（2026-08-09 已定位）**：上游 `58.19.43.66:1111` 对直连分片做 UA/防盗链检查——curl 无 Referer+普通 UA → 200；curl Chrome UA 或 `live.zbds.top` Referer → 404；浏览器 `<video>` 播放列表能加载但分片 `Failed to fetch` → err=4 黑屏。**必须分片也走本地代理**
- 浏览器：**Chrome 原生 HLS 仅支持 fMP4 分片**（2026-08-09 实测：mux VOD fMP4 可播、IPTV MPEG-TS 源 rs=0 不可播）→ **必须用 hls.js**（TS→fMP4 转封装，vendored 本地文件 + `GET /hls.min.js` 路由）；`##EXT-X-VERSION:3` 双井号等瑕疵需代理侧清洗
- `classify`（input_resolver.py）：http(s) URL → stream/page；**`.m3u` 会被当普通 stream 直连（错误路径）**——需前端识别 .m3u 后走频道面板流程
- 音频：`<video>` muted + 隐藏 `audioEl`（B站 DASH 分离音频）；**HLS 直播音视频混在同一 TS 流**，无独立音频
- `test_webui_hls.py` 断言改写结果（`https://cdn.example.com/live/channel/seg_001.ts`）——**改写目标变为代理 URL 后需同步更新测试**
- 实测源样例：`https://live.zbds.top/tv/iptv4.m3u`；CCTV1 = `http://58.19.43.66:1111/tsfile/live/1000_1.m3u8?key=txiptv&playlive=1&authid=0`（LIVE：MEDIA-SEQUENCE 持续、无 ENDLIST；分片带 query 参数）

## 1. 需求（grill 定案）

### R1 m3u 频道列表输入（第四种输入类型）
- 前端 `connect()` 检测 `.m3u` 后缀 → 调后端 `GET /channels?url=` 解析 → 打开「📺 频道」面板（分组列表、可滚动、**点击频道行即开播**）；`#url` 粘贴单个频道 m3u8 照常走现有直连流程
- 后端 `GET /channels?url=`：URL 或本地路径（os.path.exists）→ 拉取/读取 m3u → 解析 `#EXTINF`（频道名、`group-title`、下一行 URL）→ 返回 `[{group, name, url}]`；m3u 每次请求实时拉取（不缓存，源可随时更新）
- 「📺 频道」按钮可随时重开面板（重新从 `#url` 加载最新频道列表）

### R2 分片代理化 + hls.js（核心修复，Q3 实测修正：原生 HLS 不支持 TS 分片）
- `_rewrite_hls_playlist` 输出改为**本地代理 URL**：`/video?url=<urlencode(绝对上游 URL)>`（普通分片行与 `URI="..."` 属性一致处理），浏览器只与 `127.0.0.1:8000` 同源通信
- 播放列表清洗：`##EXT` → `#EXT` 归一化（实测源存在双井号行）
- **hls.js 播放内核**：vendored `tools/webui_static/hls.min.js`（本地路由 `GET /hls.min.js`，GFW 下不依赖 CDN）；m3u8 源经 `Hls.isSupported()` 走 hls.js（`liveSyncDurationCount:3` 降延迟，网络/媒体错误自动恢复）；非 m3u8 源保持原生 `<video>` src
- LIVE 播放列表保持每次实时拉上游（浏览器轮询刷新自然生效）；VOD 行为不变
- 分片 Content-Type 透传上游（video/mp2t 等），Range 透传已支持

### R3 音频：HLS 源视频元素直接出声
- 前端按 URL 判断：m3u8 源 → `player.muted = false`（视频元素自带音轨出声，**不设置 audioEl**）；其它（B站 DASH / 本地文件 / 直接 mp4）→ 维持现状（muted + audioEl）
- 切换源时按新 URL 类型重置 muted/audioEl 状态

### R4 连通性检测（用户补充需求）
- 后端 `GET /probe?url=<频道m3u8>`：httpx GET 播放列表（3s 超时，**同代理头**）测 TTFB → 2xx 且含 `#EXTINF` → 拉**首个分片前 16KB**（4s 超时）确认真能出流（防"列表能拉、分片 404"误报）→ `{ok, latency_ms}`；结果**内存缓存 5 分钟**（按 url）
- 前端频道面板：「🔍 批量检测」按钮**手动触发**，并发 20 路逐频道调用 /probe，**每测完一个立即更新对应行**（✅ 123ms / ❌ 失效，异步显示）；每行另有单个「检测」按钮；未检测频道不标注；顶部显示检测进度

### R5 直播字幕链路（复用 + 真实源验证）
- /ws 直播链路（PyAV 拉 m3u8 + 实时速率 + ready 就绪开播）已实现，本票用真实源端到端验证
- 频道播放即把频道 m3u8 当作 url 走现有 connect()/WS 流程

## 2. 验收

1. 新回路 `test/repro_live_iptv.py`（真实源，失败返回 2 注明网络原因）：
   - 粘贴 m3u → 「📺 频道」面板出现分组频道列表（≥300 频道）
   - 点击 CCTV1 → 视频播放（t 前进）+ 有声音（非 muted）+ 字幕命中（引擎 aliyun-funasr）
   - 批量检测：手动触发后逐条异步标注（抽样断言：检测中的行数增长、结束时有 ✅/❌ 标注）
2. `test_webui_hls.py`：更新为代理 URL 改写断言（相对行/URI 属性 → `/video?url=`）+ 双井号清洗断言
3. `test/repro_m3u8.py`（mux VOD）保持 GREEN（分片代理化后回放仍正常）
4. e2e_webui 5/5 回归绿（B站 DASH / 本地文件不受影响）
5. pytest + pylint 0

## 3. 范围外（不做）

- 自动换台 / 断流自动重连（hls.js 网络/媒体错误自动恢复已覆盖基础抖动）
- 音轨转码（AC3 等浏览器不支持的编码）
- 频道搜索/收藏
