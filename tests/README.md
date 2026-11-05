# 测试与验收

以下命令从仓库根目录运行，使用已安装项目依赖、`pytest` 和 `playwright` 的 Python 环境。

## 自动回归

```sh
python -m pytest -v
node --test tests/frontend/*.test.cjs
```

`pyproject.toml` 将默认 pytest 收集限定为 `tests/python/`，并配置源码与验收工具的导入路径。Python 回归包含 HTTP/WebSocket、合成媒体和验收工具 CLI，使用替身服务，不需要真实 Key。前端回归使用 Node.js；CI 使用 22。

运行单项：

```sh
python -m pytest tests/python/test_original_captions.py -v
node --test tests/frontend/caption_layout.test.cjs
```

## 浏览器旅程

`browser/` 内的 `e2e_*.py` 单独执行，不由 pytest 自动收集。`e2e_original_*.py` 使用离线识别夹具；先确认它们开头声明的前提。夹具入口是 `acceptance/original_release_fixture.py`，频道与播放时钟旅程另有自己的媒体夹具。

WebUI 只使用 `127.0.0.1:8002`。替换为夹具前核对占用进程和活动会话，完成后恢复普通服务；这些脚本不能直接向正常服务安装测试凭证。模拟识别和真实浏览器分别记录，不能据此宣称真实识别质量通过。

## 验收工具

| 工具 | 用途 |
| --- | --- |
| `acceptance/run_real_browser_acceptance.py` | 实际播放器、可见字幕和可选声音采集；见[采集指南](../docs/guides/real-browser-collector.md) |
| `acceptance/run_original_acceptance.py` | 原文字幕会话与事件记录 |
| `acceptance/score_original_acceptance.py` | 固定参考文字准确率评分 |
| `acceptance/score_acoustic_acceptance.py` | 独立声音与画面字幕同步评分 |
| `acceptance/fixed_hls_startup_acceptance.py` | 固定原速 HLS 启动采样 |
| `acceptance/fixed_realtime_startup_acceptance.py` | 固定实时输入准备与启动采样 |
| `acceptance/run_webui_with_startup_probe.py` | 加入 PCM 时钟探针的 WebUI 入口 |
| `acceptance/run_long_acceptance_webui.py` | 加入有界长测探针的 WebUI 入口 |
| `acceptance/local_live_replay.py` | 本地新闻媒体与已有识别结果回放 |
| `acceptance/prepare_original_acceptance_media.py` | 准备有输入身份的参考素材 |
| `acceptance/prepare_acoustic_review.py` | 准备独立听审材料 |
| `acceptance/manual_cloud_smoke.py` | 合成视频与替身识别/翻译的手动浏览器夹具服务，不调用真实云端 |

真实识别会产生费用，只在有授权、明确输入和运行上限时执行。采集与评分工具支持 `--help`；服务入口需先查看文件开头的配置前提。结果默认写到被忽略的 `logs/` 等目录，原始失败和 unknown 结论保留。

## 历史复现

`legacy/` 保存旧界面、旧翻译模式与 `repro_*.py`、`diag_words.py` 等一次性诊断脚本。部分脚本有旧端口、旧路径或真实服务调用，属于历史参考；复用前须核对当前界面、8002 单实例约束与云端授权。它们不进入默认回归，也不代表当前支持范围。
