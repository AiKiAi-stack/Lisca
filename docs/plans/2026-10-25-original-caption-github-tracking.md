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

最新直播失败与外部验收缺口见[真实验收](2026-10-24-original-caption-real-acceptance.md)及[实施状态](2026-10-23-original-caption-implementation-status.md)。

2026-10-26接续：当前集成提交`666b595`已推送至PR#47。启动测量辅助已完成离线验证，仍未新增真实启动批次或改变3秒阈值。`0d4fb4d`已修复原生HLS崩溃，`e67253b`修复独立Spec审查的三项问题；精确合并树Python247通过/1Windows跳过、Node102通过，远端三版本Test和Pylint均通过。Standards补审无硬性违例；Spec原三项独立复核确认解决，新增计时校准误差项由`c4e5efd`修复后再次确认解决。真实门槛保持开放，CI成功不关闭#43/#44/#46或父规格#34。


当前代码候选已推进到`ba4f806`：本机256通过/1Windows跳过、Node102通过，恢复观察和WAV启动改动均有公开先失败后通过回归。独立Spec复审确认新增发现解决，Standards没有硬性违例。中文/英文各一次真实固定冷启动为10.8094/8.4048秒（各1例，不是20次P95），3秒门槛未通过；两路1880秒上限的真实恢复长测正在运行，#40/#43/#44/#46和父规格继续保持开放。


### 固定启动方法更正（`63a8f86`）

上文10.8094/8.4048秒来自固定节奏HTTP WAV，时间与采样真实，但它没有进入原生HLS及浏览器直播分支；不能据此判定PRD的HLS首条3秒通过或失败。此前按直播门槛解读的文段以此更正为准，数字与原录音保留为普通远端WAV诊断。`63a8f86`增加实际容器/URL的HLS观察、采集路径与严格HLS来源校验门槛；20个快速WAV样本误过关的公开回归先失败后通过。20次真正HLS启动仍未测，阈值、稳定短句规则和输入选择均未调整，真正固定HLS方法正在补齐。两路正在运行的公网HLS长测是另一个批次，不受此方法问题影响。


### 2026-10-26：长测已结束

此前进行中的两路恢复观察均已结束，首次POST后约1881秒由上限取消。英文无记录错误；中文一次媒体网络错误后5.018秒出现下一连接事件，失败与恢复分别留证，不记为无错通过。50 ms采样有效、两行溢出0；未录音，95%有声覆盖与长期声学延迟增长仍未验证。完整数字和证据见[真实验收最终记录](2026-10-24-original-caption-real-acceptance.md)。固定HTTP WAV历史诊断不能判定HLS首条3秒；原20次HLS门槛保持开放。


### 2026-10-26：HLS试探与最终代码候选

代码候选推进到`fe272d9`：真正固定HLS方法`8cbdfe1`，实际解码环境/完整响应/全部AAC包校验`d3a68cb`，页面预检查顺序修复`d426754`，共享测试退出清理`fe272d9`。集成Python276通过/1Windows跳过（82.85秒，上游警告1），Node102通过、全仓错误级pylint及后续改动文件检查通过。HLS20项在PyAV17/19均通过；两轴独立复核没有剩余阻塞，短小runtime映射重复仍只作低置信度提示。

实际每语一次HLS冷启动的来源、服务环境、采样与关联均有效；中文/英文首稳定字幕保守上界10607.4/6958.6 ms，各1POST、错误0、确认DELETE200。两者均未达到3秒，未追加其余38次付费调用，不能称20次P95验收。此前0POST的预检查失败和普通WAV历史结果均保留。两路31分钟恢复观察也已结束，中文一次网络错误后的恢复与失败分别记录；声学增长和有声覆盖仍未核验。详见[真实验收](2026-10-24-original-caption-real-acceptance.md)。

`d3a68cb`远端push三版本Test/Pylint成功，但同提交PR的Python3.11在观众退出清理断言失败，其他两项被取消；未混用成功运行掩盖失败。`fe272d9`改为在测试框架取消任务前等待实际退订，无延长超时、无绕过断言。局部在Python3.10/3.12各30次通过，独立双轴复核无阻塞；最终提交的完整远端矩阵另核对。#34/#40/#43/#44/#46保持开放，#42/#45继续按用户要求延期，PR#47保持draft。
