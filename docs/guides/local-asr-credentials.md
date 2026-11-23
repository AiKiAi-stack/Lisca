# 本机 ASR 凭据

本机授权的 DashScope Key 保存在 `~/.screts/lisca.env`，变量名为 `DASHSCOPE_API_KEY`。目录权限为 `0700`，文件权限为 `0600`。后续本机识别任务从该文件读取；公开文档保留读取方式。

WebUI 使用 `http://127.0.0.1:8002/`。服务启动后，可以将文件中的 Key 应用到当前进程的内存配置：

```bash
python3 - <<'PY'
import json
import shlex
import urllib.request
from pathlib import Path

values = {}
for line in (Path.home() / '.screts' / 'lisca.env').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#'):
        name, value = line.split('=', 1)
        values[name.strip()] = shlex.split(value)[0]
request = urllib.request.Request(
    'http://127.0.0.1:8002/api/cloud',
    data=json.dumps({'api_key': values['DASHSCOPE_API_KEY'], 'save': False}).encode(),
    headers={'Content-Type': 'application/json'},
    method='PUT',
)
with urllib.request.urlopen(request, timeout=5) as response:
    result = json.load(response)
    print('configured:', result.get('configured'))
PY
```

应用配置后再执行明确输入和运行上限的识别任务。检查连接与实际识别的费用由该 Key 所属账户承担。停止任务后保留可复核的会话与关闭记录。

如果本机通过 HTTP 代理访问 DashScope，可在启动 WebUI 时显式为安全 WebSocket 指定同一路由。将以下命令用于已经设置 `https_proxy` 的终端，由项目虚拟环境执行：

```bash
env wss_proxy="$https_proxy" WSS_PROXY="$https_proxy" python -m lisca --host 127.0.0.1 --port 8002
```

2026-11-20，本机安装的 SDK 在仅设置 `https_proxy` 时对 WSS 选择了直连。显式指定代理后，一段 8 秒中文参考音频在 0.340 秒收到服务端任务确认，1.134 秒收到首段文字。
