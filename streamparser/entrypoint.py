# encoding:utf-8
import argparse
import logging
import signal
import sys
from pathlib import Path
from threading import Event, Thread
from typing import Optional

import yaml

from .config import StreamSettings
from .logger import Loggers
from .reader import StreamReader
from .utils import FileUtils, FormatterUtils, StreamAnalyzer
from .writer import StreamWriter

LOGGER = Loggers().get_logger(__name__)


class StreamProcessor:
    """流处理器"""

    def __init__(self, settings: StreamSettings) -> None:
        self.settings = settings
        self.reader = StreamReader(settings)
        self.writer = StreamWriter(settings)
        self._process_thread: Optional[Thread] = None
        self._clean_thread: Optional[Thread] = None
        self._running = Event()
        self._waiting = Event()

    def start(self) -> bool:
        try:
            available_details = StreamAnalyzer.analyze_stream_available(self.settings)
            LOGGER.info(
                f"流连接详情: {FormatterUtils.format_json_string(available_details)}"
            )
            healthy_details = StreamAnalyzer.analyze_stream_healthy(self.settings)
            LOGGER.info(
                f"流健康报告: {FormatterUtils.format_json_string(healthy_details)}"
            )
            if not (available_details.state and healthy_details.state):
                return False

            if not self.reader.start() or not self.writer.start():
                return False

            self._running.set()
            self._waiting.clear()
            self._process_thread = Thread(target=self._process, name="_process")
            self._process_thread.start()
            self._clean_thread = Thread(target=self._clean, name="_clean")
            self._clean_thread.start()
            LOGGER.info("启动成功")

            return True

        except Exception as e:
            LOGGER.error(f"启动失败: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        self._running.clear()
        self._waiting.set()
        threads = [self._process_thread, self._clean_thread]
        for thread in threads:
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
        try:
            self.reader.stop()
        except Exception as e:
            LOGGER.error(f"流读取停止失败: {e}")
        try:
            self.writer.stop()
        except Exception as e:
            LOGGER.error(f"流写入停止失败: {e}")

    def _process(self) -> None:
        LOGGER.info("开始处理")
        while self._running.is_set():
            if bool(self.reader.queue_size or self.writer.queue_size):
                try:
                    frame = self.reader.get_frame()
                    if frame:
                        self.writer.put_frame(frame)
                except Exception as e:
                    LOGGER.error(f"处理错误: {e}")
        else:
            LOGGER.info("停止处理")

    def _clean(self) -> None:
        LOGGER.info("开始清理")
        while self._running.is_set():
            if FileUtils.check_directory_exist(self.settings.directory):
                try:
                    if not self._waiting.wait(3600):
                        FileUtils.clean_oldfiles(directory=self.settings.directory)
                except Exception as e:
                    LOGGER.error(f"清理错误: {e}")
        else:
            LOGGER.info("停止清理")


def create_settings(source: str) -> Optional[StreamSettings]:
    """从配置源创建设置"""
    try:
        if Path(source).suffix in [".yml", ".yaml"]:
            with open(file=source, mode="r", encoding="utf-8") as yaml_file:
                config = yaml.safe_load(yaml_file)
            return StreamSettings.from_dict(config)
        else:
            return StreamSettings(url=source)
    except Exception as e:
        LOGGER.error(f"创建设置失败: {e}")
        return None


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="流媒体处理器")
    parser.add_argument("source", help="配置文件路径或流媒体URL")
    parser.add_argument("-d", "--debug", action="store_true", help="启用调试日志")
    args = parser.parse_args()

    if args.debug:
        LOGGER.setLevel(logging.DEBUG)

    # 创建流处理器设置
    settings = create_settings(args.source)
    if not settings:
        return 1

    # 启动流处理器程序
    processor = StreamProcessor(settings)
    try:
        if not processor.start():
            return 1
        signal.pause()
    except KeyboardInterrupt:
        LOGGER.info("接收到中断信号，正在停止")
    finally:
        processor.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
