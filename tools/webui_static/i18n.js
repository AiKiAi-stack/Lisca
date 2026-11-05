/* UI language is independent of recognition language and caption text. */
(function (root) {
  'use strict';
  const messages = {
  "跳到播放器": "Skip to player",
  "内容": "Library",
  "频道侧栏": "Channel sidebar",
  "收起频道侧栏": "Collapse channel sidebar",
  "收起": "Collapse",
  "管理频道列表": "Manage channels",
  "示例频道": "Example channels",
  "最近观看": "Recently watched",
  "清空": "Clear",
  "公共直播源，可能暂时不可用": "Public streams may be unavailable",
  "播放过的频道会显示在这里": "Channels you watch will appear here",
  "仅保留在本次页面": "Kept only for this page session",
  "打开一段内容": "Open something to watch",
  "直播与本地视频，保留语音原文": "Live streams and local videos, captioned in their original language",
  "本地视频": "Local video",
  "直播链接": "Live stream",
  "粘贴直播链接，或输入本机文件路径": "Paste a stream URL or enter a local file path",
  "结束观看": "End viewing",
  "停止播放与识别": "Stop playback and recognition",
  "Lisca · 实时字幕": "Lisca · Live captions",
  "Lisca（观众）": "Lisca · Viewer",
  "界面语言": "Interface language",
  "1. 选择内容": "1. Choose content",
  "2. 播放与字幕": "2. Playback & captions",
  "M3U 频道列表地址": "M3U channel playlist URL",
  "Windows 加密保存": "Encrypted on Windows",
  "中文": "Chinese",
  "中文语音出中文字幕，英文语音出英文字幕": "Chinese speech, Chinese captions. English speech, English captions.",
  "云端原文字幕": "Cloud captions",
  "云端服务": "Cloud service",
  "仅播放（停止识别）": "Play without captions",
  "仅本次运行": "This run only",
  "会话日志": "Session log",
  "位置": "Position",
  "使用 FunASR 识别音频，字幕保留语音原文。音频会发送给百炼，费用由你的 API 账户承担；本模式不调用翻译服务。": "FunASR transcribes the audio in its original language. Audio is sent to DashScope and billed to your API account. This mode does not use translation.",
  "停止识别": "Stop recognition",
  "全屏": "Fullscreen",
  "全屏显示视频与原文字幕": "Show video and captions in fullscreen",
  "关闭": "Close",
  "关闭字幕记录": "Close caption history",
  "关闭设置": "Close settings",
  "关闭诊断日志": "Close diagnostic log",
  "关闭频道列表": "Close channel list",
  "删除配置": "Delete configuration",
  "加载频道": "Load channels",
  "原文字幕": "Original-language captions",
  "原文字幕 · 自动识别": "Captions · Auto-detect",
  "原文字幕 · 中文": "Captions · Chinese",
  "原文字幕 · English": "Captions · English",
  "填写百炼 API Key，不会放入链接或日志": "Enter your DashScope API key; it stays out of URLs and logs",
  "复制日志": "Copy log",
  "复制观众链接": "Copy viewer link",
  "字体": "Font",
  "字号": "Size",
  "字幕": "Captions",
  "字幕 开": "Captions on",
  "字幕 关": "Captions off",
  "字幕按语音时间显示完整短句；长句按时间分段，完整识别内容可在字幕记录查看。": "Captions follow speech timestamps. Long sentences are split into shorter phrases; the caption history keeps the full transcript.",
  "字幕服务": "Caption service",
  "字幕样式": "Caption style",
  "字幕记录": "Caption history",
  "宋体": "Song / serif",
  "实时字幕": "Live captions",
  "对比度": "Contrast",
  "尚未选择内容": "No content selected",
  "已填写来源": "Source entered",
  "尚未配置": "Not configured",
  "应用配置": "Apply configuration",
  "开关原文字幕": "Toggle captions",
  "开始播放后，可将只读链接分享给同一局域网内的设备。": "Once playback starts, share a read-only link with devices on your LAN.",
  "恢复默认：字号 30px / 位置 12% / 白字半透明黑底 / 字体系统默认": "Reset to 30px / 12% / white on translucent black / system font",
  "打开内容，生成原文字幕": "Open a stream or video to add captions",
  "打开文件": "Open file",
  "打开直播或本地视频，自动识别语音语言": "Open a stream or local video. Speech language is detected automatically.",
  "批量检测所有频道的连通性与延迟（手动触发，并发 20，逐条显示）": "Check all channels for availability and latency (20 at a time)",
  "播放并生成字幕": "Play with captions",
  "断句灵敏度：语音后静音多久判定一句结束（越小切句越频繁，仅 API 引擎生效）": "Silence needed to end a sentence. Shorter values split speech more often (API engines only).",
  "断句：宽松 1300ms（服务端默认）": "Relaxed · 1300ms (server default)",
  "断句：极灵敏 200ms": "Very sensitive · 200ms",
  "断句：标准 800ms": "Standard · 800ms",
  "断句：灵敏 400ms": "Sensitive · 400ms",
  "旧演示模式的同步缓冲设置": "Legacy demo playback buffer",
  "更多": "More",
  "未加载频道列表": "No channel list loaded",
  "检查中…": "Checking…",
  "检测中…": "Checking…",
  "检测该频道连通性与延迟": "Check this channel for availability and latency",
  "检查连接（会调用云端 API）": "Check connection (uses cloud API)",
  "楷体": "Kai / calligraphic",
  "演示字幕（无识别，仅验证链路）": "Demo captions (no recognition)",
  "白字 · 半透明黑底": "White on translucent black",
  "白字 · 实黑底": "White on black",
  "直播源": "Stream",
  "直播链接或本地文件路径": "Stream URL or local file path",
  "等宽": "Monospace",
  "粘贴直播链接，或打开本地视频": "Paste a stream URL, or open a local video",
  "系统默认": "System default",
  "自动识别": "Auto-detect",
  "自动识别（原语言输出）": "Auto-detect (original language)",
  "观众设备所在局域网": "LAN address for viewers",
  "设置": "Settings",
  "设置分类": "Settings tabs",
  "识别": "Recognition",
  "识别声音，保留原话": "Live streams. Original words.",
  "诊断日志": "Diagnostic log",
  "语音断句": "Sentence splitting",
  "语音语言": "Speech language",
  "请先在百炼开通 fun-asr-realtime。检查连接不代表实际语音效果；费用以服务商账单为准。": "Enable fun-asr-realtime in DashScope first. A connection check does not verify recognition quality. Your provider's bill determines the charges.",
  "请求失败": "Request failed",
  "退出全屏": "Exit fullscreen",
  "选择频道": "Select channel",
  "通过 Windows 加密保存到本机（默认仅本次运行）": "Save with Windows encryption (otherwise this run only)",
  "配置已删除，云端会话已停止": "Configuration deleted; cloud sessions stopped",
  "重置默认": "Reset defaults",
  "重试字幕": "Retry captions",
  "阿里云百炼（中国内地）": "DashScope (mainland China)",
  "音视频延迟 100ms": "Playback delay · 100ms",
  "音视频延迟 1s": "Playback delay · 1s",
  "音视频延迟 5s": "Playback delay · 5s",
  "频道列表": "Channel list",
  "黄字 · 实黑底": "Yellow on black",
  "黑体": "Hei / sans-serif",
  "🔍 批量检测": "🔍 Check all",
  "日志已复制": "Log copied",
  "加载频道列表…": "Loading channel list…",
  "频道列表为空（源可能失效）": "The channel list is empty; the source may be unavailable",
  "❌ 失效": "❌ Unavailable",
  "❌ 检测失败": "❌ Check failed",
  "⚠ 该源播放卡顿（带宽不足或源损坏），请点 🔍 检测换一个快源": "⚠ This stream has stalled. Use 🔍 to check another channel.",
  "播放卡顿：请换源": "Playback stalled; try another source",
  "较早字幕记录未保留，当前直播继续": "Older captions are no longer retained. Live playback continues.",
  "正在准备字幕…": "Preparing captions…",
  "⏳ 跳转中，字幕将随转写跟进…": "⏳ Seeking; captions will follow as transcription catches up…",
  "该位置还没有准备好字幕。继续会从目标位置重建识别，并可能再次调用 API/产生费用。是否继续？": "Captions are not ready at this position. Continuing restarts recognition from here and may incur additional API charges. Continue?",
  "识别连接已关闭；继续播放时将从暂停位置重新连接": "Recognition is disconnected. Resuming playback reconnects from the paused position.",
  "请输入流地址": "Enter a stream URL",
  "无法读取播放列表": "Unable to read the playlist",
  "正在建立云端识别会话…": "Starting a cloud recognition session…",
  "正在识别音频，原文字幕就绪后播放…": "Recognizing audio; playback starts when captions are ready…",
  "连接中…": "Connecting…",
  "直播源恢复失败（已重试 2 次），请重新加入或联系主机": "Stream recovery failed after 2 retries. Rejoin or contact the host.",
  "直播源发生无法自动恢复的错误，请重新加入或联系主机": "The stream cannot recover automatically. Rejoin or contact the host.",
  "媒体和识别共用的恢复额度已用完；可重试字幕或仅播放": "The shared media/recognition retry budget is exhausted. Retry captions or play without them.",
  "直播源发生无法自动恢复的错误；可重试字幕或仅播放": "The stream cannot recover automatically. Retry captions or play without them.",
  "⚠ 直播流播放失败（源可能失效，换个频道）": "⚠ This stream could not play. Try another channel.",
  "已连接，引擎加载中…": "Connected; loading the engine…",
  "已识别到语音，原文字幕已就绪": "Speech detected; captions are ready",
  "播放已开始，正在识别并翻译；当前句可能修订": "Playback started; recognition and translation are running. The current sentence may change.",
  "暂未识别到语音，继续播放": "No speech detected yet; playback continues",
  "原文字幕已就绪，开始播放": "Captions ready; starting playback",
  "播放已开始，正在识别并翻译…": "Playback started; recognizing and translating…",
  "字幕已就绪，开始播放": "Captions ready; starting playback",
  "字幕记录达到安全容量上限，识别已停止": "Caption history reached its safe capacity; recognition stopped",
  "识别连接暂时中断；继续播放时会按恢复预算重新连接": "Recognition was interrupted. Resuming playback retries within the recovery budget.",
  "识别连接暂时中断，直播画面继续播放…": "Recognition was interrupted; live playback continues…",
  "出错": "Error",
  "原文字幕已生成，可播放或跳转": "Captions complete; you can play or seek",
  "音频处理结束": "Audio processing finished",
  "识别已取消；已准备字幕仍可回看": "Recognition cancelled; prepared captions remain available",
  "识别未完成，可重试或仅播放": "Recognition is incomplete. Retry or play without captions.",
  "识别结束状态未确认，可重试或仅播放": "Recognition completion could not be confirmed. Retry or play without captions.",
  "识别连接失败，自动恢复额度已用完；可手动重试或仅播放": "Recognition connection failed and automatic retries are exhausted. Retry manually or play without captions.",
  "识别连接异常关闭，自动恢复额度已用完；可手动重试或仅播放": "Recognition disconnected and automatic retries are exhausted. Retry manually or play without captions.",
  "连接失败": "Connection failed",
  "观众人数已满（最多 3 台设备），请稍后再试": "The viewer limit (3 devices) has been reached. Try again later.",
  "直播已结束或连接已断开": "The live session ended or the connection was lost",
  "识别 15 秒没有进展，已停止新音频处理。可重试或仅播放。": "Recognition made no progress for 15 seconds. New audio processing stopped. Retry or play without captions.",
  "等待当前片段的原文字幕…": "Waiting for captions for this segment…",
  "字幕暂不可用，部分缺失；媒体继续播放，完整结果见记录": "Some captions are unavailable; playback continues. See the transcript for available results.",
  "字幕已就绪，继续播放": "Captions ready; resuming playback",
  "原文字幕已追上当前播放位置": "Captions have caught up with playback",
  "等待下一段字幕确认…": "Waiting for the next caption segment…",
  "视频加载失败": "Video failed to load",
  "直播源缓冲中…": "Buffering stream…",
  "字幕准备中，就绪后自动播放…": "Preparing captions; playback will start automatically…",
  "⏳ 等待确认跳转；原字幕处理已暂停…": "⏳ Waiting for seek confirmation; caption processing is paused…",
  "仅播放；字幕暂不可用，识别已停止": "Playing without captions; recognition stopped",
  "观众链接已复制，可发给同一局域网内的设备": "Viewer link copied. Share it with devices on your LAN.",
  "复制此局域网观众链接：": "Copy this LAN viewer link:",
  "正在停止云端处理…": "Stopping cloud processing…",
  "已停止": "Stopped",
  "正在加入直播…": "Joining the live session…",
  "未选择文件": "No file selected",
  "浏览器未允许全屏，请使用浏览器全屏菜单": "Fullscreen was not allowed. Try your browser's fullscreen menu.",
  "开始播放后，完整字幕会保存在这里。": "The full transcript appears here once playback starts.",
  "部分字幕积压，完整内容见记录": "Some captions are queued; see the transcript for the full text",
  "已配置 · Windows 加密保存": "Configured · Encrypted on Windows",
  "已配置 · 仅本次运行": "Configured · This run only",
  "Key 格式无效": "Invalid key format",
  "请填写阿里云百炼 API Key": "Enter your DashScope API key",
  "请先配置云端 API Key": "Configure your cloud API key first",
  "请先填写 Key": "Enter your API key first",
  "此平台仅支持当前运行期间使用 Key，不支持持久保存": "This platform keeps keys in memory for this run only; persistent storage is unavailable",
  "无法访问 Windows 加密配置，请删除已保存配置后重新填写 Key": "Cannot read the Windows-encrypted configuration. Delete it and enter your key again.",
  "已保存配置无法读取，请删除后重新配置": "Cannot read the saved configuration. Delete it and configure it again.",
  "云端认证失败：请检查中国内地 API Key、模型权限和服务开通状态": "Cloud authentication failed. Check your mainland-China API key, model access and service activation.",
  "云端限流或额度不足：请检查账户余额与配额后重试": "Cloud rate limit or quota reached. Check your balance and quota, then retry.",
  "配置格式无效": "Invalid configuration format",
  "无法保存云端配置": "Unable to save the cloud configuration",
  "会话格式无效": "Invalid session format",
  "请选择自动识别、中文或英文原文字幕": "Choose automatic detection, Chinese or English captions",
  "请输入直播地址或选择本地视频": "Enter a stream URL or select a local video",
  "播放位置无效": "Invalid playback position",
  "首版仅支持本地视频跳转": "Seeking is currently supported for local videos only",
  "待处理会话过多，请先停止当前会话": "Too many pending sessions. Stop the current session first.",
  "会话参数无效": "Invalid session parameters",
  "识别服务已发起连接；实际认证和语音识别将在播放时验证": "The recognition connection has started. Authentication and recognition are verified during playback.",
  "识别服务连接失败，请检查 Key、权限和网络": "Could not connect to recognition. Check your key, permissions and network.",
  "缺少直播地址": "A stream URL is required",
  "直播已结束或链接无效": "The live session ended or the link is invalid",
  "会话已过期或已使用，请重新开始": "The session expired or was already used. Start again.",
  "这是单路 HLS 播放流，不是频道列表；请直接播放该地址，频道列表请使用 IPTV M3U 清单。": "This is a single HLS stream. Play the URL directly; use an IPTV M3U playlist to select channels.",
  "媒体拉流失败：上游拒绝或无法提供媒体": "The upstream source refused the request or could not provide media",
  "平台页面解析失败：请检查链接、访问权限和解析器支持情况": "Could not resolve the platform page. Check the URL, access permissions and resolver support.",
  "媒体拉流失败：请检查来源、网络和访问权限": "Could not read media. Check the source, network and access permissions.",
  "频道列表读取失败：请检查地址、网络和访问权限": "Could not read the channel list. Check its URL, your network and access permissions.",
  "识别服务认证失败，请检查 API Key": "Recognition authentication failed. Check your API key.",
  "识别服务权限不足，请检查模型权限或服务开通状态": "Recognition permission denied. Check model access and service activation.",
  "识别额度或账单条件不满足，请检查百炼账户额度": "Recognition quota or billing requirements were not met. Check your DashScope account.",
  "识别请求触发服务限流，可稍后重试": "Recognition rate limit reached. Try again later.",
  "识别网络连接暂时中断，可重试": "The recognition network connection was interrupted. You can retry.",
  "识别服务暂不可用，可稍后重试": "Recognition is temporarily unavailable. Try again later.",
  "识别失败；现有服务商证据不足，原因未知": "Recognition failed. The available provider information does not establish the cause.",
  "音频时间轴存在过大间隙或跳变，已停止识别；可更换来源或仅播放": "The audio timeline has a large gap or jump. Recognition stopped; change source or play without captions.",
  "输入地址无法识别：请提供有效 URL 或本机已存在的文件路径": "Source not recognized. Provide a valid URL or an existing local file.",
  "输入中没有音频轨，未启动识别": "The source has no audio track; recognition was not started.",
  "无法打开媒体流：请检查来源、网络和访问权限；未启动识别": "Could not open media. Check the source, network and permissions. Recognition was not started.",
  "输入音频解码失败，未启动识别": "Audio could not be decoded; recognition was not started.",
  "输入没有可解码的音频内容，未启动识别": "The source has no decodable audio; recognition was not started.",
  "媒体拉流或音频解码失败，已停止后续识别": "Media loading or audio decoding failed; further recognition stopped.",
  "识别连续 15 秒没有字幕或静音处理进展，已停止新音频处理；可重试、仅播放或停止": "Recognition made no caption or silence-processing progress for 15 seconds. New audio processing stopped; retry, play without captions or stop.",
  "识别达到安全容量上限，结果不完整；已停止识别，请重新开始": "Recognition reached its safe capacity and stopped with incomplete results. Start again.",
  "字幕记录达到安全容量上限，结果不完整；已停止识别，请停止或重新开始": "Caption history reached its safe capacity. Recognition stopped with incomplete results; stop or start again.",
  "语音识别服务异常：请检查 Key、模型权限和网络后重试": "Recognition service error. Check your key, model permissions and network, then retry.",
  "识别任务未能正常关闭，请停止后重试": "Recognition did not close normally. Stop and retry.",
  "后台识别任务达到安全上限，已有任务仍在关闭；请稍后重试": "The background recognition limit was reached while earlier tasks are closing. Try again shortly.",
  "无法创建后台识别任务，请稍后重试": "Could not create a background recognition task. Try again later.",
  "观众链接无效或已绑定到其他设备": "The viewer link is invalid or is bound to another device.",
  "观众字幕事件积压达到安全上限，连接已结束；请重新加入直播": "The viewer caption backlog reached its safe limit. The connection closed; rejoin the live session.",
  "主播端直播处理发生错误": "The host encountered a live-processing error.",
  "分享记录不完整": "The shared history is incomplete."
};
  const formats = [
    ['共 {count} 个频道，选择后再开始播放', '{count} channels. Choose one before playback.'],
    ['频道列表 {count} 个，选择后再开始播放', '{count} channels loaded. Choose one before playback.'],
    ['已选择 {name}，点击“播放并生成字幕”开始观看', 'Selected {name}. Click “Play with captions” to watch.'],
    ['上传 {name}…', 'Uploading {name}…'],
    ['批量检测 {done}/{total}', 'Checking channels {done}/{total}'],
    ['批量检测完成 {done}/{total}', 'Channel checks complete {done}/{total}'],
    ['未分组（{count}）', 'Ungrouped ({count})'],
    ['正在恢复直播源（{attempt}/{max}，{seconds} 秒后）…', 'Recovering stream ({attempt}/{max}; retry in {seconds} s)…'],
    ['正在恢复识别（{attempt}/{max}，{seconds} 秒后）…', 'Recovering recognition ({attempt}/{max}; retry in {seconds} s)…'],
    ['识别连接已结束，{seconds} 秒后继续恢复（{attempt}/{max}）…', 'Recognition disconnected; retrying in {seconds} s ({attempt}/{max})…'],
    ['错误码 {code}（容器/编码不受支持或链接失效）', 'Error {code} (unsupported format/codec or unavailable URL)'],
    ['云端服务暂不可用（HTTP {status}），请稍后重试', 'Cloud service unavailable (HTTP {status}). Try again later.'],
    ['译文滞后 {seconds}s', 'Translation is {seconds}s behind']
  ];
  const prefixes = [
    ['频道列表加载失败：', 'Could not load the channel list: '],
    ['频道列表解析失败：', 'Could not parse the channel list: '],
    ['分享链接不可用：', 'Viewer link unavailable: '],
    ['来源检查失败：', 'Source check failed: '],
    ['无法创建观众链接：', 'Could not create a viewer link: '],
    ['媒体和识别共用的恢复额度已用完：', 'The shared recovery budget is exhausted: '],
    ['语音识别失败：', 'Speech recognition failed: '],
    ['⚠ 视频无法播放：', '⚠ Video could not play: '],
    ['原文字幕部分缺失或暂不可用，直播媒体继续播放；', 'Some captions are missing or unavailable; live playback continues. '],
    ['上传失败: ', 'Upload failed: '],
    ['⚠ ', '⚠ ']
  ];
  const skip = 'script,style,svg,textarea,video,audio,#history,#current,#selectionStatus,.ch-name,.ch-group,[data-i18n-skip]';
  function pattern(format) {
    const names = [];
    const parts = format.split(/(\{[a-z]+\})/g).map(part => {
      if (/^\{[a-z]+\}$/.test(part)) { names.push(part.slice(1, -1)); return '([\\s\\S]+?)'; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    });
    return {regex: new RegExp('^' + parts.join('') + '$'), names};
  }
  const templates = formats.map(pair => ({pair, patterns: pair.map(pattern)}));
  const reverse = Object.fromEntries(Object.entries(messages).map(([zh, en]) => [en, zh]));
  function createI18n(host) {
    const normalize = value => /^en(?:-|$)/i.test(value) ? 'en' : /^zh(?:-|$)/i.test(value) ? 'zh' : null;
    let saved = null;
    try { saved = normalize(host.localStorage?.getItem('lisca-ui-language')); } catch (_) { /* Storage is optional. */ }
    let language = normalize(new URLSearchParams(host.location?.search || '').get('lang')) || saved || normalize(host.navigator?.language) || 'zh';
    const bindings = new WeakMap();
    function t(value) {
      const text = String(value ?? '');
      const trimmed = text.trim();
      if (!trimmed) return text;
      if (text !== trimmed) return text.replace(trimmed, t(trimmed));
      if (language === 'en' && Object.hasOwn(messages, text)) return messages[text];
      if (language === 'zh' && Object.hasOwn(reverse, text)) return reverse[text];
      const from = language === 'en' ? 0 : 1, to = 1 - from;
      for (const {pair, patterns} of templates) {
        const match = text.match(patterns[from].regex);
        if (!match) continue;
        const values = Object.fromEntries(patterns[from].names.map((name, index) => [name, match[index + 1]]));
        return pair[to].replace(/\{([a-z]+)\}/g, (_, name) => values[name]);
      }
      for (const pair of prefixes) {
        if (text.startsWith(pair[from])) return pair[to] + t(text.slice(pair[from].length));
      }
      return text;
    }
    function setText(element, value) {
      if (!element) return;
      const source = String(value ?? '');
      const rendered = t(source);
      element.textContent = rendered;
      bindings.set(element, {source, rendered});
    }
    function apply(node = host.document) {
      if (!node) return;
      const old = bindings.get(node);
      if (old && node.textContent === old.rendered) { setText(node, old.source); return; }
      if (old) bindings.delete(node);
      if (node.nodeType === 3) {
        const text = node.nodeValue;
        const trimmed = text.trim();
        if (trimmed) setText(node, text);
        return;
      }
      if (node.nodeType === 1) {
        for (const attr of ['placeholder', 'title', 'aria-label']) {
          if (!node.hasAttribute(attr)) continue;
          const key = 'lisca-' + attr;
          const source = node.dataset[key.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] || node.getAttribute(attr);
          node.dataset[key.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = source;
          node.setAttribute(attr, t(source));
        }
        if (node.matches(skip)) return;
      }
      for (const child of Array.from(node.childNodes || [])) apply(child);
    }
    function setLanguage(value) {
      language = normalize(value) || 'zh';
      try { host.localStorage?.setItem('lisca-ui-language', language); } catch (_) { /* Storage is optional. */ }
      const doc = host.document;
      if (doc) {
        doc.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
        apply(doc);
        doc.title = t(doc.title);
        const control = doc.getElementById('uiLanguage');
        if (control) control.value = language;
      }
      if (host.history?.replaceState && host.location?.href) {
        const url = new URL(host.location.href); url.searchParams.set('lang', language);
        host.history.replaceState(null, '', url);
      }
    }
    function start() {
      const ready = () => {
        const doc = host.document;
        // This field later contains filenames/channel names, which stay unchanged.
        setText(doc.getElementById('selectionStatus'), doc.getElementById('selectionStatus')?.textContent || '');
        setLanguage(language);
        const control = doc.getElementById('uiLanguage');
        if (control) control.addEventListener('change', () => setLanguage(control.value));
      };
      if (host.document?.readyState === 'loading') host.document.addEventListener('DOMContentLoaded', ready, {once: true});
      else if (host.document) ready();
    }
    function link(value) {
      const url = new URL(value, host.location?.href); url.searchParams.set('lang', language); return url.href;
    }
    return {t, setText, apply, setLanguage, start, link, forget: element => bindings.delete(element), get language() { return language; }};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {createI18n, messages};
  if (root.document) { root.LiscaI18n = createI18n(root); root.LiscaI18n.start(); }
})(typeof globalThis !== 'undefined' ? globalThis : this);
