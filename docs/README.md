# 项目导航

## 目录职责

```text
lisca/               应用代码，安装后的 Python 包与命令行入口
  asr/               识别客户端、识别进程和字幕数据类型
  engines/           识别引擎实现
  web/               WebUI 后端、static/ 页面资源与 licenses/ 许可证
  _vendor/           随附的 whisper_streaming 上游代码
tools/               音频提取、诊断和 CUDA 启动辅助脚本
tests/
  python/            pytest 自动回归（包括 HTTP、WebSocket 和工具 CLI）
  frontend/          Node.js 自动回归
  browser/           单独执行的真实浏览器旅程
  acceptance/        素材准备、采集、计时、回放、评分与离线夹具
  legacy/            旧界面、旧翻译模式与一次性复现脚本
examples/            配置、调用示例与公共频道列表
docs/
  guides/            操作与开发指南
  reference/         外部接口参考资料的存档
  adr/               已确认的产品与设计决策
  plans/             规格、计划与各日期的实施状态
  research/          调查、验收结论及参考素材清单
  assets/            README 使用的图片与品牌资源
  agents/            Issue、标签与领域文档约定
  archive/           历史规格、交接和旧工具包说明
```

根目录保留安装配置、许可证、双语 README、Windows 启动入口、`AGENTS.md` 与 `GLOSSARY.md`。现有启动命令仍为 `python -m lisca --host 127.0.0.1 --port 8002`。

## 按任务查找

| 要做的事 | 入口 |
| --- | --- |
| 安装、使用、报告问题 | [中文 README](../README_zh.md) / [English README](../README.md) |
| 理解当前产品范围 | [原文字幕 PRD](plans/2026-10-21-live-translation-prd.md)、[实现规格](plans/2026-10-23-original-caption-release-spec.md) |
| 理解字幕行为 | [术语](../GLOSSARY.md)、[ADR-0008](adr/0008-original-language-captions.md) |
| 核对预览验收与正式发布缺口 | [ADR-0009](adr/0009-pr89-preview-acceptance-2026-11-04.md)、[PR89 复测](plans/2026-11-04-pr89-acceptance-recheck.md)、[GitHub Issues](https://github.com/AiKiAi-stack/Lisca/issues) |
| 运行回归、浏览器或真实验收 | [测试说明](../tests/README.md) |
| 采集真实播放器证据 | [浏览器采集指南](guides/real-browser-collector.md) |
| 本地新闻回放 | [回放方法与证据](research/p0-local-news-2026-11-04.md) |
| 离线整段转写 | [FunASR Vulkan 指南](guides/funasr-vulkan.md) |
| 查看页面设计背景 | [UI 设计说明](guides/ui-design.md) |
| 查阅随附的云端协议资料 | [FunASR 参考存档](reference/fun_asr_realtime_recognition.md) |
| 了解早期实现与交接 | [旧规格](archive/SPEC.md)、[旧术语与决策索引](archive/CONTEXT.md)、[2026-10-25 交接](archive/handoff.md) |

日期文档记录对应候选和输入的结论。当前预览通过与正式发布通过分别核对，不能用历史记录或自动回归替代仍未完成的真实观看验收。

## 本地数据与新增文件

`logs/`、`uploads/`、`.data/`、`.cache/`、模型和虚拟环境是忽略的本地数据；目录整理不迁移或清理这些内容。源码启动继续使用仓库内的上传与配置目录；安装 wheel 后默认在当前工作目录保存。可用 `LISCA_DATA_DIR` 指定上传和配置的共同数据目录。`.scratch/original-caption-release/issues/` 保留已跟踪的历史任务，当前工作跟踪使用 GitHub Issues。

新增自动回归放入对应的 `tests/python/` 或 `tests/frontend/`；需显式运行的浏览器旅程和验收工具分别放入 `tests/browser/`、`tests/acceptance/`。新指南放 `docs/guides/`，调查和带日期的执行记录沿用 `research/`、`plans/`，失效的开发入口归 `archive/`。

新增应用代码放在 `lisca/`，使用 `lisca.*` 导入。`tools/` 不承载业务实现，测试配置也不再把它加入运行时导入路径。WebUI 入口为 `python -m lisca`，字幕 CLI 为 `python -m lisca.stream_subs`，Windows 环境准备入口为 `python -m lisca.windows_launcher`。

2026-11-09 核心迁移：原 `tools/` 的应用实现、`streamparser/` 和 `whisper_streaming/` 分别归入 `lisca/`、`lisca/asr/` 和 `lisca/_vendor/`。旧脚本路径与 `streamparser.*` 导入改用上述包入口；仓库内的调用、测试和启动器已同步。

2026-11-05 目录迁移：原 `test/<文件名>` 已按上述用途归类，文件名保留；根目录的 `SPEC.md`、`CONTEXT.md`、`handoff.md` 已移至 `docs/archive/`，`notes/real-browser-collector.md` 已移至 `docs/guides/`。历史提交和报告中的路径以其候选版本为准。
