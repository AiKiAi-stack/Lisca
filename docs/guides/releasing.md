# 发布二进制

`Release binaries` Actions 为 Windows x64 和 Linux x64 构建带 Python 运行时的便携包。应用使用云端识别，`yt-dlp` 命令与运行库随包提供。

## 发布流程

1. 创建版本标签与 GitHub Release。`release: published` 自动构建该标签的代码。
2. 两个平台分别打包、解压并运行产物。检查音频解码、识别 SDK 导入、spawn 子进程、`yt-dlp`、8002 端口 HTTP、页面资源和上传存储。
3. 两个平台检查通过后，publish 作业把两个便携包上传到 Release。

已有版本补传或重建时，从 Actions 手动运行 `Release binaries`，填入原标签，并勾选 `publish`。例如：

```sh
gh workflow run release.yml -f tag=v0.1.0 -f publish=true
```

标签对应应用源码；运行工作流的提交提供打包入口和检查脚本。包内 `build-info.json` 记录这两个提交及依赖版本，重建时保留来源。校验和或完整性需求可直接使用 GitHub Release asset 的 SHA-256 digest。

## 使用

- Windows：解压 ZIP，运行 `Lisca.exe`。
- Linux：解压 tar.gz，运行 `./Lisca`。构建环境为 Ubuntu 22.04 x64，运行环境需 glibc 2.35 或更新版本。
- 打开 `http://127.0.0.1:8002/`，配置 DashScope Key。保留完整解压目录，关闭运行窗口即可停止。
- 数据默认保存在可执行文件旁的 `data/`；可用 `LISCA_DATA_DIR` 指定目录。`Lisca --check` 执行离线安装诊断。

CI 验证的是便携包启动和本机功能。真实语音质量、字幕同步和长时体验继续按对应 Issue 的证据判断。

## 本地构建

使用 Python 3.12，源码参数指向所需版本的干净 checkout：

```sh
python -m pip install -r requirements-webui.txt concurrent-log-handler pyinstaller==6.22.3
python tools/release/build.py --source . --output dist
python tools/release/check_binary.py dist/Lisca-0.1.0-linux-x64.tar.gz
```

Windows 构建输出 `Lisca-0.1.0-windows-x64.zip`，使用相同检查脚本。检查会使用端口 8002，先确认没有其他服务占用。
