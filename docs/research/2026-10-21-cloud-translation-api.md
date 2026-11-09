# 云端 ASR 与中英翻译 API 调研（2026-10-21）

## 范围与结论

本调研用于首版“中文语音→英文字幕、英文语音→中文字幕”的云端路线。只查阅阿里云百炼 / Qwen 一手文档并只读项目代码；没有读取 API Key、访问用户密钥文件或调用任何计费 API。厂商文档描述的是服务能力，不构成本项目效果、延迟或费用的实测结论。

**建议首版复用项目已有的 Fun-ASR-Realtime，再通过 Qwen OpenAI-compatible Chat Completions 翻译识别文字。** 翻译优先评估专用的 qwen-mt-flash（有增量流式输出和显式源/目标语种参数），qwen-mt-plus 适合需要完整翻译结果的模式；一般文本模型 qwen-plus 也能走标准 Chat Completions，但语种控制需放在提示词里。首版界面只承诺中→英、英→中。

另有官方单路实时音频翻译模型 qwen3.8-livetranslate-flash-realtime。它可在一个 WebSocket 会话里接收音频、输出原文识别和目标语种的流式文本；但它使用另一套 Realtime 事件协议，而且文档事件没有给字幕可直接使用的音频时间戳字段。因此它适合作为之后的对照路线，当前实现不必依赖它。

## 项目现状与适配点

项目已有 [AliyunFunASREngine](../../lisca/engines/aliyun_funasr.py)，默认模型是 fun-asr-realtime，底层复用 [ASRClient](../../lisca/asr/asr_client.py) 和 DashScope Python SDK。识别回调已保留句子文本、句/字起止时间、is_final 和 sentence_id，WebUI 也已有 partial/final 的字幕事件路径。

当前引擎构造参数的 language_hints 默认是 ("zh",)；虽然构造器也有 language 参数，但它没有传给识别 SDK。WebUI 创建阿里云引擎时目前只传入断句静音时长。英→中验收需要将识别源语言配置传到引擎，并将 language_hints 设为 ("en",)，或省略 hint 让服务自动检测。每个识别任务只能指定一个 hint。用户提供 Key 的首版流程也需要接入当前 WebUI；当前启动代码从环境变量读取 Key，并兼容开发者的 ~/Projects/.env 文件。

项目的 [ADR-0004](../adr/0004-buffered-audio-video-and-final-captions.md) 已使用 ASR 字/句时间戳控制字幕与播放位置。翻译返回的是目标文本，官方 Qwen-MT 响应不提供译文逐词时间轴；应以源句的 ID 和时间范围绑定译文，不能假定译文词与原文字逐项对应。

## Fun-ASR-Realtime：当前 ASR 路线可用

### 请求与输入限制

Fun-ASR-Realtime 使用 DashScope WebSocket Inference 协议。北京区域业务空间域名为：

~~~text
wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference
~~~

鉴权头为 Authorization: Bearer <API_KEY>。连接建立后发送 run-task，等到 task-started 后才能送音频；音频以二进制帧上传，结束时发送 finish-task。核心请求字段如下，省略 UUID 和不需要的可选项：

~~~json
{
  "header": {
    "action": "run-task",
    "task_id": "<UUID>",
    "streaming": "duplex"
  },
  "payload": {
    "task_group": "audio",
    "task": "asr",
    "function": "recognition",
    "model": "fun-asr-realtime",
    "input": {},
    "parameters": {
      "format": "pcm",
      "sample_rate": 16000,
      "language_hints": ["zh"]
    }
  }
}
~~~

模型支持的实时输入格式包括 PCM、WAV、MP3、Opus、Speex、AAC 和 AMR；WAV 必须是 PCM，Opus/Speex 必须使用 Ogg 封装。普通 Fun-ASR-Realtime 可接受任意采样率和不限时长；8 kHz 专用模型则必须使用 8 kHz。模型清单列出 fun-asr-realtime 为多语种、方言实时模型，fun-asr-realtime-2026-02-28 支持中文、英文、日文；产品首版仍建议只公开 zh/en 两个来源。官方说明 language_hints 不设置时自动识别，设置时只允许一个值。详见[模型能力与语言表](https://help.aliyun.com/zh/model-studio/asr-model/)及[客户端事件参考](https://help.aliyun.com/en/model-studio/fun-asr-client-events)。

### 中间结果、终稿与时间戳

服务端通过 result-generated 返回识别。当前句中间结果用 sentence_end=false，句末终稿为 sentence_end=true；新句首个中间事件用 sentence_begin=true。句级 begin_time/end_time 和字级 words[].begin_time/end_time 都以毫秒表示；中间结果的句末时间可以为 null，句子终结时才有稳定终点。用 sentence_id 可关联同一句的多次修订。服务端还会发 task-started、task-finished、task-failed；失败事件含 error_code/error_message，且连接会关闭，不能复用。事件结构见[服务端事件参考](https://help.aliyun.com/zh/model-studio/fun-asr-server-events)。

这与项目现有 ASR 回调模型基本匹配，主要缺口是把源语言选择接入设置，并在翻译层保留相同句子的身份与时间范围。

## Qwen 文本翻译：OpenAI-compatible Chat Completions

### 一般文本模型 qwen-plus

北京默认业务空间的 HTTP 地址为：

~~~text
POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
~~~

北京子业务空间推荐使用：

~~~text
POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions
~~~

请求头是 Authorization: Bearer <API_KEY> 和 Content-Type: application/json。例如普通 qwen-plus 文本请求：

~~~json
{
  "model": "qwen-plus",
  "messages": [
    {
      "role": "system",
      "content": "Translate the user's sentence into English. Return only the translation."
    },
    {
      "role": "user",
      "content": "我看到这个视频后没有笑。"
    }
  ],
  "stream": false
}
~~~

标准响应从 choices[0].message.content 取译文；响应对象有 id、model、choices、usage 等字段。使用 qwen-plus 是通用文本生成：API 没有专用的 source_lang/target_lang 参数，源/目标语言及“只返回译文”等约束放在 prompt。阿里云文档示例确认该模型通过同一兼容端点调用；模型语言能力说明涵盖中英文，但不能把通用聊天模型能力当作任意语言组合的翻译 SLA。参见[OpenAI 兼容调用文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[qwen-plus 模型信息](https://help.aliyun.com/zh/model-studio/qwen-plus)。

### 专用翻译模型 qwen-mt-flash / qwen-mt-plus

专用 Qwen-MT 同样通过兼容 Chat Completions 调用。北京子业务空间地址与上例相同；新加坡地址为 https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions。地区 Key 与服务区域需要匹配。

qwen-mt-plus、qwen-mt-flash、qwen-mt-lite 和 qwen-mt-turbo 都在官方 API 支持列表。Qwen-MT 文档列出 92 种互译语言；请求需且仅需一个 user message。源语言 source_lang 与目标语言 target_lang 使用支持表里的英文语言名称，例如 Chinese / English；源语言可设为 auto。中英两种方向都有明确源/目标字段。建议应用按已选方向传 Chinese→English 或 English→Chinese，减少自动识别方向误差。实际产品仍只公开两种组合，其他语种不随模型清单自动承诺。

HTTP 请求示例：

~~~json
{
  "model": "qwen-mt-flash",
  "messages": [
    {
      "role": "user",
      "content": "我看到这个视频后没有笑。"
    }
  ],
  "translation_options": {
    "source_lang": "Chinese",
    "target_lang": "English"
  },
  "stream": false
}
~~~

translation_options 是 Qwen 扩展参数；HTTP 请求直接放顶层。Python OpenAI SDK 需用 extra_body={"translation_options": ...}，Node SDK 则可顶层传入。非流式响应仍是标准 Chat Completion，取 choices[0].message.content。官方文档显示 qwen-mt-flash 和 qwen-mt-lite 在 stream=true 时逐块返回新增文本；qwen-mt-plus 每个 chunk 返回从开头到当前的完整前缀，客户端不能把它误当新增片段 append。参见[Qwen-MT API 参考](https://help.aliyun.com/en/model-studio/qwen-mt-api)和[Qwen-MT 支持语种及调用方式](https://help.aliyun.com/zh/model-studio/machine-translation/)。

**建议：** 对需要逐步显示的当前句，优先验证 qwen-mt-flash 的流式输出；如只在源句稳定/终稿后翻译，则先用 qwen-mt-plus 非流式完整结果更直接。无论如何，把译文事件绑定到 ASR 的 sentence_id；过期句或已更正的待处理返回不得覆盖当前位置。API 的 chunk 只代表一次翻译请求的流式生成，不表示模型能自动跟随多次 ASR partial 修订；重复提交变化中的整句会产生额外请求和费用，建议先用稳定短句 / 终稿节流，并通过本地样本测量首条可读译文和终稿延迟。

## 错误、超时和 Key 权限

OpenAI-compatible 文档列明常见状态：400 请求无效、401 Key 无效、429 速率上限或额度不足、500 服务错误、503 服务过载；应把配置/认证问题与可重试的过载区分开。通用错误码文档列出大模型请求超时 500-RequestTimeOut，说明调用大模型的超时报错时间为 300 秒，并建议使用流式输出；这个时限远长于实时字幕可接受的等待，应用应另设较短、可配置的 HTTP connect/read timeout，并丢弃超时的过期句结果。Qwen 服务失败时可保留 request ID 供用户诊断；不要记录 API Key 或将其放入 URL。子业务空间调用 qwen-plus 等标准模型还需开通对应模型权限。参见[兼容调用状态码](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[百炼错误码](https://help.aliyun.com/zh/model-studio/error-code)、[子业务空间调用说明](https://help.aliyun.com/zh/model-studio/model-calling-in-sub-workspace)。

Fun-ASR 的 WebSocket 错误走 task-failed，服务端给错误码和消息并关闭连接；官方示例包含任务超时报错，因此客户端需让结束、错误和断连都有有限的收敛路径。项目现有 ASR 客户端会把回调错误放入队列，但翻译新增的 HTTP/WebSocket 错误仍需进入用户可见状态，并遵循 PRD 的有限重试、停止新音频提交、停止会话时不再产生新请求等要求。

## 单接口语音翻译候选：Qwen3.8 LiveTranslate Realtime

模型 qwen3.8-livetranslate-flash-realtime 使用 Realtime WebSocket，区别于 Fun-ASR 的 Inference WebSocket：

~~~text
wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-livetranslate-flash-realtime
Authorization: Bearer <API_KEY>
~~~

连接后可发送如下会话设置，再持续用 input_audio_buffer.append 发送 Base64 音频：

~~~json
{
  "type": "session.update",
  "session": {
    "output_modalities": ["text"],
    "translation": {
      "language": "en"
    }
  }
}
~~~

模型自动识别源语种，原文识别始终启用，且原文 transcript 和译文输出在事件中可关联；纯文本译文用 response.text.delta 和 response.text.done，目标语种可设为 en 或 zh 等代码。默认输出模态含 audio，应设 ["text"] 避免字幕模式还输出配音。服务文档称支持 60 种源语言、29 种语言的语音输出，并宣称同传时延可低至 2.3 秒；这些是阿里云产品能力声明，未在本机实测。具体参见[实时语音/音视频翻译指南](https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime)、[Qwen3.8 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-8-livetranslate-flash-realtime)、[客户端事件](https://help.aliyun.com/en/model-studio/live-translator-client-events)和[服务端事件](https://help.aliyun.com/en/model-studio/live-translator-server-events)。

这条路线适合以“输入音频、输出翻译”为核心的全新实时链路，避免分别维护 ASR 与翻译 API；但实现上需新增 session.update、Base64 音频事件、VAD/说话人检测、事件关联和 realtime 重连处理。已查的服务端事件格式提供文本事件和 item/response ID，没有 Fun-ASR 的 begin_time/end_time 或逐字时间戳字段。因此它不能仅凭 API 文档替换当前按源音频时间戳驱动的字幕管线；需要专门的本地样本实验验证分句、字幕时间映射、跳转和停止重开行为。当前首版两种语向可用现有 ASR 时间轴加 Qwen-MT 满足，等基线证明时延不足再评估该方案。

## 已核验的官方一手来源

- [Fun-ASR 实时/录音识别模型、规格与语言](https://help.aliyun.com/zh/model-studio/asr-model/)
- [Fun-ASR-Realtime WebSocket 客户端事件](https://help.aliyun.com/en/model-studio/fun-asr-client-events)
- [Fun-ASR-Realtime WebSocket 服务端事件及时间戳](https://help.aliyun.com/zh/model-studio/fun-asr-server-events)
- [Qwen OpenAI-compatible Chat Completions 调用与状态码](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [Qwen-MT API 请求、响应、模型名与流式行为](https://help.aliyun.com/en/model-studio/qwen-mt-api)
- [Qwen-MT 支持语种与工作方式](https://help.aliyun.com/zh/model-studio/machine-translation/)
- [Qwen-plus 模型信息](https://help.aliyun.com/zh/model-studio/qwen-plus)
- [百炼错误码与超时说明](https://help.aliyun.com/zh/model-studio/error-code)
- [Qwen 实时翻译 WebSocket 指南](https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime)
- [Qwen 实时翻译模型信息](https://help.aliyun.com/zh/model-studio/qwen3-8-livetranslate-flash-realtime)
- [Qwen 实时翻译客户端事件](https://help.aliyun.com/en/model-studio/live-translator-client-events)
- [Qwen 实时翻译服务端事件](https://help.aliyun.com/en/model-studio/live-translator-server-events)
