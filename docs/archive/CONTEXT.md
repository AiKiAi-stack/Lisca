# CONTEXT.md — 实时字幕 webUI 领域词汇与决策索引

> 历史存档。当前术语见 [GLOSSARY.md](../../GLOSSARY.md)，后续决策见 [ADR 目录](../adr/)。

grill-with-docs 维护。新增术语/决策时保持与 ADR、SPEC 一致。

## 术语

- **字幕消息（subtitle message）**：后端经 WebSocket 推给前端的事件，字段 `{type:"subtitle", beg, end, text, words?}`。
- **partial / 中间结果**：ASR 对未完成句子输出的累计文本事件（`sentence_end=false`，句终前 `end_time` 可能为 null）。
- **final / 句终结果**：`sentence_end=true`，服务端补全最终 `end_time`。
- **句级时间戳**：`sentence.begin_time/end_time`（毫秒，音频时间轴）。
- **字级时间戳（words）**：`sentence.words[]`，每字 `{begin_time, end_time, text, punctuation}`；官方定位用于字幕对齐。
- **断句（VAD）**：服务端判定句子结束。fun-asr-realtime 由 `max_sentence_silence`（静音阈值 ms）控制，默认 1300，范围 [200, 6000]。
- **对齐**：`beg/end`（引擎音频时间轴）与播放进度（video 元素时间轴）的匹配；当前为隐式对齐（1.5s 首播补偿 + 播放即同步 + 容差）。

## 决策索引

- ADR-0001：字幕显示模型——字级渲染为主、断句灵敏度可调、隐式对齐（ticket-10 / issue #10）；显示部分已被 ADR-0002 取代。
- ADR-0002：字幕显示模型 v2——固定窗口分块（1-4s 可调），窗口内完整显示、到点整体切换或空白（ticket-13 / issue #15）。
