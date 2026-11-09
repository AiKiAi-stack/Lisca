# encoding:utf-8
import logging
import logging.config
import os
from copy import deepcopy
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import IO, Any, List, Optional

from concurrent_log_handler import ConcurrentRotatingFileHandler


class LazyDirectoryHandler(ConcurrentRotatingFileHandler):
    """只在写日志时创建目录的处理器"""

    def do_open(self, mode=None) -> IO[Any]:
        """打开日志文件前确保目录存在"""
        os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
        return super().do_open(mode)

    def _open_lockfile(self) -> None:
        """打开锁文件前确保其目录存在"""
        os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
        return super()._open_lockfile()


DEFAULT_LOGGING_DIR = Path(os.getcwd()) / "logs"

DEFAULT_LOGGING_DICT: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # 详细：包含级别、时间、名称、进程号、线程号、文件名、模块名、函数名、行号和消息
        "detail": {
            "format": "[%(levelname)s] - [%(asctime)s.%(msecs)03d] - [%(name)s] - [%(process)d %(thread)d %(filename)s %(module)s %(funcName)s %(lineno)d] - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        # 简洁：仅包含级别、时间、名称和消息
        "sample": {
            "format": "[%(levelname)s] - [%(asctime)s.%(msecs)03d] - [%(name)s] - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "NOTSET",
            "formatter": "sample",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "()": LazyDirectoryHandler,
            "level": "NOTSET",
            "formatter": "sample",
            "filename": str(DEFAULT_LOGGING_DIR / "app.log"),
            "maxBytes": 10485760,
            "backupCount": 20,
            "encoding": "utf8",
        },
    },
    "root": {
        "level": "INFO",
        "handlers": [
            "console",
            "file",
        ],
    },
}


logging.config.dictConfig(DEFAULT_LOGGING_DICT)
LOGGER = logging.getLogger(__name__)


class LoggingLevel(IntEnum):
    """日志级别"""

    NOTSET = logging.NOTSET  # 初始化设置，表示日志器级别未设置，将继承父级别
    DEBUG = logging.DEBUG  # 提供详细信息，主要用于开发过程中的调试
    INFO = logging.INFO  # 记录正常的运行时事件，帮助跟踪程序运行流程
    WARNING = logging.WARNING  # 表示可能发生的异常或意外情况，程序仍在正常工作
    ERROR = logging.ERROR  # 因特定运行时事件导致的严重问题，但不一定导致程序崩溃
    CRITICAL = logging.CRITICAL  # 对于非常严重的错误事件，通常会导致程序无法继续运行


class LoggingHandler(IntEnum):
    """日志处理器"""

    CONSOLE = 0
    FILE = 1


@dataclass
class Loggers:
    """日志器"""

    logging_dir: str = field(default_factory=lambda: str(DEFAULT_LOGGING_DIR))
    logging_dict: dict = field(default_factory=lambda: deepcopy(DEFAULT_LOGGING_DICT))
    logging_level: str = field(default="INFO")
    logging_handlers: List[str] = field(default_factory=lambda: ["console", "file"])
    verbose: bool = field(default=False)

    def __post_init__(self) -> None:
        """初始化后处理"""
        self._setup_logger()

    def _setup_logger(self) -> bool:
        """设置日志器"""
        try:
            self.logging_dict["root"]["level"] = self.logging_level
            self.logging_dict["root"]["handlers"] = self.logging_handlers
            for handler in self.logging_dict["handlers"].values():
                if "filename" in handler:
                    handler["filename"] = os.path.join(self.logging_dir, "app.log")

            if self.verbose:
                for handler in self.logging_dict["handlers"].values():
                    if "formatter" in handler:
                        handler["formatter"] = "detail"

            logging.config.dictConfig(self.logging_dict)
            return True

        except Exception as e:
            LOGGER.error(f"[{e}]: failed to setup logger.")
            raise

    def get_logger(
        self,
        name: Optional[str] = None,
        level: Optional[str] = None,
        handlers: Optional[List[str]] = None,
    ) -> logging.Logger:
        """获取日志实例"""
        try:
            logger = logging.getLogger(name) if name else logging.getLogger()
            if level:
                logger.setLevel(LoggingLevel[level.upper()])
            if handlers:
                logger.propagate = False
                logger.handlers.clear()
                for handler in handlers:
                    logger.addHandler(
                        logging.getLogger().handlers[LoggingHandler[handler.upper()]]
                    )
        except Exception as e:
            LOGGER.error(f"[{e}]: failed to get logger.")
            raise

        return logger
