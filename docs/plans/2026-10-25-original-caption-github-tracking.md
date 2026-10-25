# 原文字幕：GitHub规格、任务与实施映射

2026-10-25（Asia/Shanghai）。用户明确要求按Matt `to-spec → to-tickets → implement/implement-spec` 继续，并更新GitHub Issue、commit、PR。2026-10-23已确认的规格、公开测试边界、12项任务粒度和依赖原样承接，不重新访谈或另建范围。

集成PR：[draft #47](https://github.com/AiKiAi-stack/stream-with-asr/pull/47)。分支已实际推送；不是仅本地草稿。

父规格：[34 · 原文字幕首版：实现规格与发布验收](https://github.com/AiKiAi-stack/stream-with-asr/issues/34)。所有Issue当前保持open；01–07的代码/离线任务完成不等于PR已合并或首版发布通过。

| 本地任务 | GitHub | 实施状态 | 阻塞任务 |
| --- | --- | --- | --- |
| 01 | [#35 配置识别服务并安全打开内容](https://github.com/AiKiAi-stack/stream-with-asr/issues/35) | completed | 无 |
| 02 | [#36 在当前语音区间显示可读的原文短句](https://github.com/AiKiAi-stack/stream-with-asr/issues/36) | completed | 无 |
| 03 | [#37 本地提前播放、静音放行与无进展退出](https://github.com/AiKiAi-stack/stream-with-asr/issues/37) | completed | 无 |
| 04 | [#38 本地暂停跳转复用字幕并隔离旧会话](https://github.com/AiKiAi-stack/stream-with-asr/issues/38) | completed | 无 |
| 05 | [#39 直播字幕故障有界等待并恢复当前时间轴](https://github.com/AiKiAi-stack/stream-with-asr/issues/39) | completed | 无 |
| 06 | [#40 统一故障恢复预算并证明停止后没有新请求](https://github.com/AiKiAi-stack/stream-with-asr/issues/40) | completed | 无 |
| 07 | [#41 把评分报告绑定到正确的识别输入](https://github.com/AiKiAi-stack/stream-with-asr/issues/41) | completed | 无 |
| 08 | [#42 局域网观众正确复用主机原文字幕](https://github.com/AiKiAi-stack/stream-with-asr/issues/42) | deferred | #39, #40 |
| 09 | [#43 完成中英文本地质量与长文件体验验收](https://github.com/AiKiAi-stack/stream-with-asr/issues/43) | in-progress | #35, #36, #37, #38, #40, #41 |
| 10 | [#44 验收中英文直播、频道旅程并建立支持矩阵](https://github.com/AiKiAi-stack/stream-with-asr/issues/44) | in-progress | #35, #36, #39, #40, #41 |
| 11 | [#45 在干净 Windows 完成安装、观看与隐私验收](https://github.com/AiKiAi-stack/stream-with-asr/issues/45) | deferred | #35, #36, #37, #38, #40 |
| 12 | [#46 核对首版证据与发布门槛](https://github.com/AiKiAi-stack/stream-with-asr/issues/46) | blocked-by-acceptance | #42, #43, #44, #45 |

已建立12条GitHub原生父子关系及22条原生阻塞依赖，34次操作全部成功；Issue正文也保留可读依赖以便核对。迁移前已查询全部现有Issue和开放PR，没有发现本规格的重复条目。旧Issue、旧Windows结论和原慢速/含糊素材判定不作改写。

实施沿用集成分支 `feat/original-caption-release`，PR先保持draft。01–07可在相应PR合并时关闭；08/11按用户原决定延期，09/10继续验收，12及父规格保持开放。不得使用自动关闭关键字把未完成验收或整份规格提前关闭。

最新直播失败与外部验收缺口见[真实验收](2026-10-24-original-caption-real-acceptance.md)及[实施状态](2026-10-23-original-caption-implementation-status.md)。实时启动观测辅助文件尚在完成中；指定Luna/Max子代理一度受额度限制，恢复后继续处理原生CI崩溃与实时启动采集。最终候选的独立双轴复审仍待运行，不记为完成。


当前集成提交包含启动测量辅助，仍未新增真实启动批次或改变3秒阈值。独立审查已运行到`0d4fb4d`：Standards 0，Spec 3项代码问题集中修复中；新辅助文件的审查补充及最终候选复审待完成。
