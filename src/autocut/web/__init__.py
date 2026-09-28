"""图形操作台：本地 HTTP 服务 + 静态页面，供不熟悉命令行的使用者操作。"""

from .server import OperatorServer, serve

__all__ = ["OperatorServer", "serve"]
