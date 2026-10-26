# 真实直播验收：来源预检

日期：2026-10-25（Asia/Shanghai）。对应正式本地任务 `.scratch/original-caption-release/issues/10-live-and-channel-acceptance.md`，不是同编号的历史GitHub Issue。

本次仅做有上限的HTTP清单与媒体片段解码，没有调用识别、试听或完成30分钟观看。PyAV 19.0.2解出一帧音频只证明短时传输/解码，不等于实际语种、字幕或长时稳定性通过。

后续补充不含识别的浏览器预检：通过现有媒体代理及hls.js独立播放浙江国际与DW，各观察约3秒前进。浙江国际1920×1080、DW480×270均达到可播放状态，无致命HLS错误；主动阻断云端路由，新增ASR请求为0。截图分别可见“浙江国际/王牌部队”和DW台标/英文新闻条；这是画面身份观察，未替代声音试听，也未验证产品的字幕旅程。原始结果与截图保存在本机持久notes目录。

## 当前候选

| 候选 | 短时结果 | 验收用途与限制 |
| --- | --- | --- |
| 浙江国际频道 | 清单295字节、片段1,043,024字节均HTTP200；AAC 44.1kHz一帧，约0.97秒 | 新增中文候选，仍需确认实际节目语种；未做浏览器或真实识别验收 |
| DW English | 清单与片段HTTP200；AAC 48kHz一帧，约4.0秒 | 英文优先候选；实际声音和30分钟状态待验 |
| Al Jazeera English | 音频帧可解码，接近15秒预检上限，媒体读取达到2MiB上限 | 英文备选，不据此宣称稳定 |
| VOA TV China | 曾短时解码成功；本轮初次清单超时，重试清单/片段200但读取1MiB未解出帧 | 当前不稳定，保留失败，不作为已验证中文来源 |
| CGTN | 重试后AAC音频可解码，约5.1秒 | 同时出现在两语列表，标签不能证明实际语种 |
| CCTV-4 Asia / CCTV-13 列表地址 | 同一第三方主机的两个地址均两次清单超时，各合计约11.1秒，未读取媒体片段 | 本轮来源失败，官方频道身份不等于该列表地址可达 |

清单按每候选最多15秒、每片段最多2MiB、清单最多8KiB进行小范围读取；中文频道发现阶段另读取44,449字节、219项的公开中文列表。没有下载完整节目录像。

## 可重复的公开地址

- 浙江国际：`https://ali-m-l.cztv.com/channels/lantian/channel10/1080p.m3u8`
- DW：`https://dwamdstream102.akamaized.net/hls/live/2015525/dwstream102/master.m3u8`
- Al Jazeera：`https://live-hls-apps-aje-fa.getaj.net/AJE/index.m3u8`
- VOA：`https://voa-ingest.akamaized.net/hls/live/2033878/tvmc08/playlist.m3u8`
- CGTN：`https://amg00405-rakutentv-cgtn-rakuten-i9tar.amagi.tv/master.m3u8`

发现来源为[iptv-org中文列表](https://iptv-org.github.io/iptv/languages/zho.m3u)及[英文列表](https://iptv-org.github.io/iptv/languages/eng.m3u)。节目与频道身份参考[DW英文直播页](https://www.dw.com/en/live-tv/channel-english)、[浙江广电集团](https://www.zmg.com.cn/)、[浙江电视直播选择页](https://zmtv.cztv.com/cmsh5-share/prod/cztv-tvLive/index.html?pageId=101)、[CCTV-4亚洲](https://tv.cctv.com/cctv4asia/m/)及[CCTV-13](https://tv.cctv.com/cctv13/)。列表由第三方维护，官方身份页只支持频道身份，不证明列表地址的稳定性。

现有界面从「设置→更多→频道列表」调用公开 `/channels` 读取列表，然后选择具体节目。后续必须分别记录列表旅程、直接直播的真实媒体和字幕状态；不能把列表200或另一语种成功替代该案例。所有候选尚未进入本轮真实原文字幕验收通过清单。
