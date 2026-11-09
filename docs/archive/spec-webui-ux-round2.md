# SPEC — webUI 可用性第二轮（round2）

状态: draft → 待 to-tickets 拆分
来源: 用户 2026-08-09 实测反馈（6 项），经主 agent 诊断回路取证

## 0. 架构事实（实现者必读）

- 后端 `lisca/web/app.py`（FastAPI）：
  - `GET /` 单页 `lisca/web/static/index.html`（no-store）
  - `GET /video?url=` / `GET /audio?url=`：本地文件→FileResponse；平台链接→yt-dlp 解析
    bv*/ba 后经 httpx 代理（补 Referer/UA）；5 分钟进程内 `_CACHE`（按 url 键）
  - `WS /ws?url&engine`：worker 线程 `StreamSource`(PyAV) 按实时速率（0.1s/chunk）
    推音频给引擎；引擎（默认 aliyun-funasr，dashscope）回 `(beg, end, text)`，
    **绝对流时间、秒**；partial 为整句累计文本（同句 beg 不变、end 随 partial 增长）
- 前端 `index.html`：`subs[]` 句子列表 + `#history` 面板 + `#current` overlay
  （absolute 叠在视频上）；`syncSubtitle()` 每 2s 用 `player.currentTime` 匹配
  时间窗 `[beg-0.15, end+0.2)`；首条字幕 +1.5s 自动播放；pause/resume 经 WS 冻结后端
- 既有回路（红/绿基准，`.venv/bin/python` 运行）：
  - `tests/legacy/repro_overlay.py [url] [earlyplay]`：单会话 overlay 持续同步
  - `tests/legacy/repro_switch.py`：停止→切换→开始，画面/声音/字幕同源断言
  - `tests/legacy/e2e_webui.py`：**旧验收**（手动 seek 后才断言 overlay —— 被用户判定失真，见 R5）
- 环境：key 由 webui main() 从 `~/Projects/.env`（dashscopetoken= 行）加载，禁硬编码；
  playwright 已装；B站可达；无系统 ffmpeg（PyAV 捆绑）；CI = pytest + pylint --errors-only
- 本轮已提交修复：9fa871e（加载期拦截手动播放；PyAV 防盗链头）、988ef4b（二次连接重置
  uiPaused/关旧 WS）。**用户在此之后仍报 R1-R3，说明问题不止这些。**

## 1. 需求

### R1 首段同步与字幕时长（B站完整视频）
- 症状：前几秒字幕异常，需拖进度条才正常；且字幕停留过长。
- 机制假设（待实现者用回路验证/推翻）：partial 的 end 随流增长 → 时间窗右界漂移，
  句子停留到下一句到达（过长）；起始偏移使首句窗口与视频 t 错位（首段异常）。
- 验收：不 seek 前提下，e2e 断言 ① 播放起 10s 内 overlay 命中前两句；
  ② 某句 end+1.5s 后 overlay 不再显示该句（不滞留）。

### R2 切换时音频源不更新（B站双链接）
- 症状：二次连接后视频源正确更新、**音频源仍是第一个**。
- 排查方向：`audioEl.src` 赋值时机/`preload`、`/audio` 的 `_CACHE`、
  旧 audioEl 未 pause/load、浏览器对失败 src 的回退。实现者须先扩展
  `repro_switch.py` 到 B站双链接复现（红）再修。
- 验收：切换后 `audioEl.currentSrc` 的 url 参数 == 第二输入，且 overlay/history
  文本来自第二音频；本地双文件场景保持绿。

### R3 本地视频不可用
- 症状：本地视频"无法正常使用"（用户未给细节；实现者须先复现：直接路径 +
  上传按钮两条路径，观察播放/overlay/报错；注意上传大文件的 multipart、
  FileResponse range、autoplay 拦截链路）。
- 验收：e2e 覆盖 ① 直接本地路径 ② upload 上传，均 overlay 同步绿。

### R4 日志反馈按钮
- webUI 加「日志」按钮：环形缓冲记录会话事件（connect/start/play/pause/seek/
  ws 消息摘要/error/status，带时间戳），面板展示 + 一键复制（textarea 兜底），
  供用户贴给开发者。
- 验收：e2e 断言按钮存在、面板含本会话事件、可取到文本。

### R5 验收标准重建
- 旧 `e2e_webui.py` 用手动 seek 掩盖同步缺陷 → 重写为真实 UX 断言：
  不 seek 连续同步、首段、切换、本地、暂停冻结保留。
- 验收：新 e2e 对修复前代码至少一条红、修复后全绿；接入方式保持单命令可跑。

### R6 m3u8 直播源
- 端到端支持 http(s) m3u8（直播）：resolve 直通、StreamSource 实时拉、
  前端播放与 overlay 对齐（直播时间轴语义以实测为准）。
- 验收：回路脚本对可达 m3u8 源（公网测试流或本地生成的 m3u8 VOD）断言
  overlay 出字幕；网络不可达时允许本地 m3u8 兜底并在脚本中注明。
- 实测可达（2026-08-09）：
  - VOD: https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8 (200)
  - LIVE: https://cph-p2p-msl.akamaized.net/hls/live/2000341/test/master.m3u8 (200)

## 2. 排序与阻塞边

T5(验收重建) → T2(音频切换) → T1(首段/时长) → T3(本地) → T4(日志按钮) → T6(m3u8)
理由：先有红基准；前端/后端同文件串行防冲突。T4/T6 与前者无数据依赖但同文件，
仍串行；若实现者能证明写范围不相交，可经协调者批准后并行。

## 3. 约束

- 每个 bug 先红回路后修复（diagnosing-bugs 纪律）；回路留在 test/ 作回归。
- 不破坏 53 个单测与 pylint CI；提交风格中文 feat/fix 前缀；不硬编码 key。
- 实现一律后台子会话（deepseek/deepseek-v4-flash），主 agent 只协调/审查。
