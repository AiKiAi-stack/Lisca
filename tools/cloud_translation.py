"""Cloud configuration and bounded, revision-aware streaming translation.

Keys stay on the local server; the browser receives only redacted capabilities.
"""
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import threading
import time

import httpx


LANGUAGES = {"zh": "Chinese", "en": "English"}
TRANSLATION_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


class CloudError(ValueError):
    """An actionable message that never includes a provider's raw response."""


class RequestStop(threading.Event):
    """Serialize cancellation acknowledgements with starting a cloud request.

    A request holding the gate is already in flight. A stop request immediately
    stops audio production; acknowledgement waits for that request's headers.
    """
    def __init__(self):
        super().__init__()
        self.request_lock = threading.Lock()
        self._requested = threading.Event()

    def set(self):
        self._requested.set()
        with self.request_lock:
            super().set()

    def is_set(self):
        return self._requested.is_set()

    def wait(self, timeout=None):
        return self._requested.wait(timeout)


@contextmanager
def _request_stream(client, cancel, **kwargs):
    context = client.stream("POST", TRANSLATION_URL, **kwargs)
    with getattr(cancel, "request_lock", nullcontext()):
        response = None if cancel.is_set() else context.__enter__()
    if response is None:
        yield None
        return
    try:
        yield response
    finally:
        context.__exit__(None, None, None)


@dataclass(frozen=True)
class CloudConfig:
    api_key: str = field(default="", repr=False)
    translation_key: str = field(default="", repr=False)

    @property
    def configured(self):
        return bool(self.api_key)

    def public(self, saved=False):
        return {"configured": self.configured, "saved": saved,
                "provider": "阿里云百炼（中国内地）", "asr_model": "fun-asr-realtime",
                "translation_model": "qwen-mt-flash",
                "language_pairs": [["zh", "en"], ["en", "zh"]],
                "persistent_storage": os.name == "nt"}


def validate_pair(source, target):
    if (source, target) not in (("zh", "en"), ("en", "zh")):
        raise CloudError("首版支持中文→英文、英文→中文，请选择受支持的语言组合")


def _protect(data, decrypt=False):
    """Windows user-bound DPAPI; never fall back to plaintext storage."""
    if os.name != "nt":
        raise CloudError("此平台仅支持当前运行期间使用 Key，不支持持久保存")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(incoming), None, None, None, None, 1,
                    ctypes.byref(outgoing)):
        raise CloudError("无法访问 Windows 加密配置，请删除已保存配置后重新填写 Key")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


class CloudConfigStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._config = CloudConfig()
        self._loaded = False

    def get(self):
        with self._lock:
            if not self._loaded:
                if self.path.exists():
                    try:
                        self._config = CloudConfig(**json.loads(_protect(self.path.read_bytes(), True)))
                    except (OSError, ValueError, TypeError):
                        raise CloudError("已保存配置无法读取，请删除后重新配置") from None
                self._loaded = True
            return self._config

    def update(self, data):
        with self._lock:
            old = self.get()
            for name in ("api_key", "translation_key"):
                if name in data and (not isinstance(data[name], str) or len(data[name]) > 512):
                    raise CloudError("Key 格式无效")
            config = replace(old, api_key=data.get("api_key", "").strip() or old.api_key,
                             translation_key=data.get("translation_key", old.translation_key).strip())
            if not config.configured:
                raise CloudError("请填写阿里云百炼 API Key")
            if data.get("save") is True:
                encrypted = _protect(json.dumps({"api_key": config.api_key,
                    "translation_key": config.translation_key}).encode())
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_bytes(encrypted)
                temporary.replace(self.path)
            else:
                self.path.unlink(missing_ok=True)
            self._config, self._loaded = config, True
            return config.public(self.path.exists())

    def clear(self):
        with self._lock:
            self.path.unlink(missing_ok=True)
            self._config, self._loaded = CloudConfig(), True
            return self._config.public()


def provider_error(status):
    if status in (401, 403):
        return CloudError("云端认证失败：请检查中国内地 API Key、模型权限和服务开通状态")
    if status == 429:
        return CloudError("云端限流或额度不足：请检查账户余额与配额后重试")
    return CloudError(f"云端服务暂不可用（HTTP {status}），请稍后重试")


class QwenTranslator:
    def __init__(self, config, source, target, transport=None):
        validate_pair(source, target)
        self.config, self.source, self.target = config, source, target
        self.client = httpx.Client(timeout=httpx.Timeout(15, connect=5), transport=transport)
        self._response = None
        self._closed = threading.Event()

    def translate(self, text, cancel):
        """Yield cumulative MT text; flash SSE content is a delta, not a prefix."""
        if cancel.is_set() or self._closed.is_set():
            return
        payload = {"model": "qwen-mt-flash", "stream": True,
                   "messages": [{"role": "user", "content": text}],
                   "translation_options": {"source_lang": LANGUAGES[self.source],
                                           "target_lang": LANGUAGES[self.target]}}
        try:
            if cancel.is_set() or self._closed.is_set():
                return
            with _request_stream(self.client, cancel, json=payload,
                headers={"Authorization": "Bearer " + (self.config.translation_key
                                                        or self.config.api_key)}) as response:
                if response is None:
                    return
                self._response = response
                if cancel.is_set() or self._closed.is_set():
                    return
                if response.status_code != 200:
                    raise provider_error(response.status_code)
                cumulative = ""
                completed = False
                started = time.monotonic()
                for line in response.iter_lines():
                    if cancel.is_set():
                        return
                    if time.monotonic() - started > 15:
                        raise CloudError("云端翻译超时，请检查网络后重试")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        completed = True
                        break
                    event = json.loads(data)
                    if "error" in event:
                        raise CloudError("云端翻译失败，请检查服务状态与额度")
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    if choices[0].get("finish_reason") == "stop":
                        completed = True
                    content = choices[0].get("delta", {}).get("content") or ""
                    if content:
                        cumulative += content
                        yield cumulative
                if not cumulative.strip():
                    raise CloudError("云端返回了空译文，请重试")
                if not completed:
                    raise CloudError("翻译连接中断，译文尚未完成，请重试")
        except httpx.TimeoutException:
            raise CloudError("云端翻译超时，请检查网络后重试") from None
        except httpx.HTTPError:
            raise CloudError("无法连接云端翻译服务，请检查网络") from None
        except (json.JSONDecodeError, KeyError, TypeError):
            raise CloudError("云端翻译响应格式异常，请重试") from None
        finally:
            self._response = None

    def close(self):
        self._closed.set()
        if self._response is not None:
            self._response.close()
        self.client.close()


class TranslationSession:
    """Coalesce partials per sentence; bound backlog and stop before more billing."""
    def __init__(self, translator, emit, stop):
        self.translator, self.emit, self.stop = translator, emit, stop
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._finalized = set()
        self._revision = 0
        self._last_partial = 0.0
        self._finishing = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, cue):
        key = cue.get("sentence_id")
        if key is None:
            key = round(cue["beg"], 2)
        with self._condition:
            if self.stop.is_set() or key in self._finalized:
                return
            if cue["is_final"]:
                self._finalized.add(key)
                if len(self._finalized) > 1000:
                    self._finalized.pop()
            if len(self._pending) >= 32 and key not in self._pending:
                self.stop.set()
                raise CloudError("翻译处理持续落后，已停止云端请求；请检查网络或稍后重试")
            self._revision += 1
            self._pending[key] = {**cue, "revision": self._revision}
            self._condition.notify_all()

    def _run(self):
        try:
            while not self.stop.is_set():
                with self._condition:
                    while not self._pending and not self._finishing and not self.stop.is_set():
                        self._condition.wait(0.2)
                    if self.stop.is_set() or not self._pending:
                        break
                    key = next(iter(self._pending))
                    cue = self._pending[key]
                    wait = 0.6 - (time.monotonic() - self._last_partial)
                    if not cue["is_final"] and wait > 0 and not self._finishing:
                        self._condition.wait(min(wait, 0.2))
                        continue
                    self._pending.pop(key)
                self._last_partial = time.monotonic()
                if self.stop.is_set():
                    break
                translated = ""
                started = time.monotonic()
                for translated in self.translator.translate(cue["text"], self.stop):
                    if self.stop.is_set():
                        break
                    self.emit({**cue, "text": translated, "source_text": cue["text"],
                               "is_final": False, "words": None, "translated": True,
                               "translation_ms": round((time.monotonic() - started) * 1000)})
                if not self.stop.is_set() and translated and cue["is_final"]:
                    self.emit({**cue, "text": translated, "source_text": cue["text"],
                               "words": None, "translated": True})
        except Exception as exc:  # service boundary: do not leak response bodies or keys
            message = str(exc) if isinstance(exc, CloudError) else "翻译服务异常，请重试"
            if not self.stop.is_set():
                self.emit({"type": "error", "message": message})
            self.stop.set()
        finally:
            self.translator.close()

    def finish(self):
        with self._condition:
            self._finishing = True
            self._condition.notify_all()
        self._thread.join(timeout=20)
        if self._thread.is_alive() and not self.stop.is_set():
            self.stop.set()
            raise CloudError("翻译队列处理超时，已停止新的云端请求")

    def close(self):
        self.stop.set()
        with self._condition:
            self._pending.clear()
            self._condition.notify_all()
        self.translator.close()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
