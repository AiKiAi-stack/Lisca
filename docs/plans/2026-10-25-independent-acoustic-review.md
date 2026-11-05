# 独立语音听审与声学标注准备

2026-10-25，Linux 本机。此步骤准备人工标注材料，**没有新增真实识别、人工听审结果或同步通过结论**。

## 已准备的材料

复用既有五分钟参考集的全部 **中文59条、英文67条** 音频，不另行挑选好结果。每份离线页面提供原速播放、波形定位、参考文字核对、多段有声区间标记、疑点记录和 JSON 导出：

- [中文标注页](../../logs/original-caption-acceptance/real-2026-10-25/listening-review/zh-reference/acoustic-review.html)
- [英文标注页](../../logs/original-caption-acceptance/real-2026-10-25/listening-review/en-reference/acoustic-review.html)

在浏览器中打开 HTML 文件；保留同目录编号 WAV。音频、页面和草稿保留在忽略目录。页面不联网、不调用识别服务，也不加载识别器词时间作为标注答案。所有标注初始为空，既有快语速素材的确认不迁移到这两套参考集。

[生成器](../../tests/acceptance/prepare_acoustic_review.py)先核验完整输入 SHA-256、每条音频 SHA-256，以及片段 PCM 与完整输入对应区间逐字节一致；波形来自原始采样幅度，不自动推断有声区间。音频时间以片段为单位显示，同时给出完整输入时间。

## 标注方法与结果边界

1. 试听并选择参考无误、需修正或听不清。修正文字单独记录，原参考保留；听不清不计确认。
2. 对照声音标出每段实际有声区间。波形点击可定位，也可以输入时间或用当前位置设置起止；存在停顿时添加多个区间。区间不得越界或重叠。
3. 完整核对后勾选本条区间标注完成，记录否定、数字、指代和串音疑点。填写标注者并定期导出 JSON；浏览器会尝试保存草稿，导出文件用于留存。

导出绑定完整输入、片段、原参考文字及标注包哈希，包含人工填写区间及其完整输入坐标。导出始终保留 `acoustic_sync_assessed: false`、`release_gate_passed: false`。计数仅反映草稿填写情况，不证明标注准确，也不表示达到发布标准。

后续须核验人工标注，再独立对照屏幕录像中的对应字幕出现、消失和覆盖区间，按 [PRD 第7节](2026-10-21-live-translation-prd.md)计算起止偏差、P50/P95/最大值、样本量及有声覆盖率。缺失字幕必须计入覆盖缺口。只标记音频无法关闭同步验收；旧英文浏览器采样最大间隔168ms、`measurement_invalid` 的限制继续保留，不因新增音频标注而转为有效。已有旧版长测也不因此变为当前代码通过。

## 本轮验证

两语全部126个片段的哈希与原 PCM 区间验证通过。完整输入哈希错配、片段哈希错配和错误源区间均被拒绝，未生成输出。实际 Chrome 检查了本地音频播放、初始未标注、多段区间、拒绝重叠、删除后取消完成状态、刷新恢复和导出内容；375px与1100px无横向溢出，页面错误0、远程请求0。导出测试使用显式 `AUTOMATED-SMOKE-ONLY-NOT-HUMAN` 标注者，测试状态已清除，没有写回原听审记录。原始检查摘要和截图在 `listening-review/acoustic-review-smoke.json` 及两语同目录 PNG。生成器错误级 pylint 通过，未修改产品或重启 WebUI。

可复现命令（输出已存在时拒绝覆盖，重建请使用新文件名）：

```bash
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python tests/acceptance/prepare_acoustic_review.py \
  --status logs/original-caption-acceptance/real-2026-10-25/listening-review/zh-reference/review-status.json \
  --media uploads/acceptance-media/zh-single-speaker-reference.wav \
  --output logs/original-caption-acceptance/real-2026-10-25/listening-review/zh-reference/acoustic-review.html
```

英文将路径中的 `zh` 替换为 `en`。本轮仅准备材料；英文新 MIT 快语速候选仍独立等待用户确认，未对未确认素材新增付费识别。
