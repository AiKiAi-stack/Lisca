# ADR-0001: 字幕显示模型——字级渲染与断句粒度

- 状态：已接受（2026-08-09，grill-with-docs 定案）
- 关联：ticket-10（issue #10）

## 背景

用户反馈：音源持续播放时，字幕在句尾瞬间消失，空窗后成段整块出现。

根因（代码 + 官方 API 文档双重确认）：

1. fun-asr-realtime 服务端 `max_sentence_silence` 默认 **1300ms**——句子要等 1.3s 静音才断句，句长可达数十秒；前端显示窗口 `[beg-0.15, end+0.2]` 在句 finalize 后立即关闭，而下一句首 partial 受引擎处理延迟影响迟滞到达 → 空窗 + 整段出现。
2. API 默认返回字级时间戳 `sentence.words[]`（`begin_time/end_time/text/punctuation`），当前实现仅取 `text`，`words` 被丢弃。

## 决策

1. **字级渲染（主）**：aliyun 引擎将 `words` 随字幕消息下发；前端当前句按播放进度逐字/逐词出现（`end_time ≤ t`），句终整句入历史区；句间定格上一句，消除空窗。
2. **断句灵敏度可调**：webUI 暴露 `max_sentence_silence` 选择（200/400/800/1300ms），默认 400ms。
3. **引擎连接稳健性**：`heartbeat=true`（直播长静音防断连，需 SDK ≥1.23.1，当前 1.26.6 满足）；`language_hints=["zh"]`。
4. **对齐策略**：维持隐式对齐（1.5s 首播补偿 + 播放即同步）；seek 后清空当前句显示；暂停冻结。
5. **范围边界**：本地 faster-whisper 引擎不改（无字级数据），前端收到无 `words` 的消息回退句级整块；`semantic_punctuation_enabled` 保持 false（语义断句句子更长，与细粒度目标相反）。

## 备选方案

- 句尾无限滞留（仅前端改动）——被否：不解决「窗口粒度太粗」的根因，句子仍整块跳变。
- 仅调 `max_sentence_silence` 切短句——被否：断句由服务端 VAD 判定，过度切短会语义破碎；字级渲染才是细粒度主杠杆。
- 显式动态偏移校准——推迟：先落地字级渲染观察实际滞后观感，明显不适再单独排期。

## 后果

- 前端显示模型从「时间窗匹配」改为「字级累积匹配」；需兜底 partial 中间结果缺尾字 `end_time`（句终才补）的情况。
- 引擎接口与 WS 字幕消息扩展可选 `words` 字段；本地引擎不填。
- 验收以 e2e 自动化断言 + 用户手动复验为准：同一句内 `#current` 文本多次增量变化、句间无 >0.5s 空窗、字幕时间窗覆盖播放进度。

## 观察项（未排期）

`speech_noise_threshold`（噪音环境判定）、`multi_threshold_mode_enabled`、`vocabulary_id`（预编译热词，精度方向）、`timestamp_alignment_enabled`（SDK kwarg 存在但官方 fun-asr-realtime 参数表未列，待实测）。
