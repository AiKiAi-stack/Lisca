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


### 2026-10-26：新增可独立修复切片

已按规格#34从时序任务#44拆出[#48：HLS输入分析预读](https://github.com/AiKiAi-stack/stream-with-asr/issues/48)，标签`ready-for-agent`，原生父任务为#44，并新增#44被#48阻塞的原生依赖。原12任务与22条依赖保留；这是追加的故障修复切片，不重开已确认规格访谈。跨PyAV17/19、两语言、单变量无识别对照共12项，完整PCM一致，分析时长限制使首body→PCM约2511ms降至2ms；公开门控回归与实现正在完成，不能提前标为已修复或端到端通过。方法和范围见[诊断报告](../research/2026-10-26-hls-decoder-startup.md)。


### 2026-10-26：#48解码预读修复

`913e161`实现[#48](https://github.com/AiKiAi-stack/stream-with-asr/issues/48)：仅在HTTP(S) HLS输入分支限制分析时长；直播分片选择、防崩溃复用防护、证书验证、短句稳定性与3秒指标保持原规则。公开生成H.264/AAC真实HLS回归先在“等待后续分片才产出PCM”处失败，修复后可在后续分片门控前取音；与独立本地完整HLS解码核对全文PCM长度/SHA-256、首PTS及分片0。源时钟仍约束首片和后续分片body。

最终树Python277通过/1Windows跳过（83.72秒，上游警告1）、全仓错误级pylint通过。PyAV19的四个来源/HTTPS/PTS/WAV/HLS文件25项通过；最终PyAV17同四文件25项通过（11.69秒），含最终独立基线、PTS及门控断言。独立Spec与Standards复核均无阻塞；Spec所要求记录的HTTPS与17/19验证现已补齐。输入分析的单变量12组诊断见[报告](../research/2026-10-26-hls-decoder-startup.md)。

普通8002已从核对所属的旧进程重启到此产品提交（PID168315），根页及5项资源与保存文件一致且HTTP200，API200、fixture404，配置仅内存；无额外WebUI或临时素材源。未新增付费识别，真正可见启动的10607.4/6958.6ms仍明确是修复前各1例，不能从解码器2ms推导3秒可见通过。#44/#46与人工、平台门槛不关闭。最终推送提交的远端矩阵随后独立核对。


### 2026-10-26：spec核对后的修复后实测与报告补修

以代码树`cc2bd36`、产品`913e161`完成中英文各一次有上限真实冷启动；当前原速输入、编码/解码及环境身份与原试探一致。中文8092.9ms、英文4554.1ms，各1POST、错误0、DELETE200、来源/采样有效，仍不满足3秒，也不构成20次P95。旧10607.4/6958.6ms保留为修复前数据。新的快照置于[实施状态](2026-10-23-original-caption-implementation-status.md)前部，分段及原始范围见[真实验收](2026-10-24-original-caption-real-acceptance.md)。

独立前沿核对未找到新的产品行为缺陷，但发现部分批次的HLS观察被20次计数污染。`a36268f`在#44范围修复这一报告问题，27项辅助及全量277通过/1Windows跳过；最终发布条件不变。独立Standards补审无违例，独立Spec补审因约定模型额度中止，未冒称双轴已完成；PR保持draft。原父子/阻塞图和已确认粒度不变，没有为同一验收重复建单。


原Luna/Max恢复后，`a36268f`的独立Spec补审已在`786ceb2`树完成，确认无错误放行；与Standards补审共同完成此次增量双轴核对。没有擅自替换模型。当前#44的支持矩阵及公开材料两项已单独勾选，真实时序等四项仍未通过，PR继续draft。#40按其已确认的实现/离线边界已完成，PR关闭列表已与01–07任务状态对齐；合并时再关闭，未提前手动关闭Issue。


## 2026-10-26：沿#44补齐当前版本频道旅程

未另开重复规格或任务：该修复对应#44原有“列表选台→媒体与原文字幕完整留证”。`0ca69c8`由独立实现工作树完成TDD，再由合并代理快进到集成分支；只在选台连接前收起频道弹窗。双轴审查无代码阻塞项，Spec明确保留完整#44的开放范围。

当前版本离线输入5/5、Node102与普通8002重启/资源核对通过后，一次真实中文固定回环HLS旅程通过：列表0POST、选台1POST200、媒体在字幕可见采样跨度推进3.054691秒、停止DELETE200、错误/翻译0。#44仅勾选频道功能旅程，其他三项核心直播门槛继续开放；#34/#43/#44/#46与PR47的draft状态不变。此新增真实案例不是20次启动样本，也不扩大公网频道支持。此前`10bc7ea`远端9项全部成功；本次新提交的CI独立核对，不借用旧绿色。
