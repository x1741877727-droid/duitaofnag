"""
雷电模拟器管理模块
通过 ldconsole 命令行工具管理多开实例
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LDInstance:
    """雷电模拟器实例信息"""
    index: int          # 实例编号
    name: str           # 实例名称
    adb_port: int       # ADB 端口
    pid: int = 0        # 进程 PID
    running: bool = False


class LDPlayerManager:
    """
    雷电模拟器管理器
    封装 ldconsole.exe 的常用操作
    """

    def __init__(self, ldplayer_path: str, mock: bool = False):
        self.ldplayer_path = ldplayer_path
        self.mock = mock
        # ldconsole.exe 路径
        self.console_path = os.path.join(ldplayer_path, "ldconsole.exe")
        # 缓存实例信息
        self._instances: dict[int, LDInstance] = {}

    async def _run_cmd(self, *args: str) -> str:
        """执行 ldconsole 命令并返回输出"""
        if self.mock:
            return self._mock_cmd(args)

        cmd = [self.console_path] + list(args)
        logger.debug(f"执行命令: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode("utf-8", errors="ignore").strip()

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip()
            logger.error(f"ldconsole 命令失败: {err}")

        return output

    def _mock_cmd(self, args: tuple) -> str:
        """Mock 模式下的命令模拟"""
        cmd = args[0] if args else ""

        if cmd == "list2":
            # 返回 6 个模拟实例
            lines = []
            for i in range(6):
                # list2 格式: index,name,top_hwnd,bindwnd,running,pid,vbox_pid
                running = 1 if self._instances.get(i, LDInstance(i, f"LDPlayer-{i}", 5555 + i * 2)).running else 0
                lines.append(f"{i},LDPlayer-{i},0,0,{running},0,0")
            return "\n".join(lines)

        elif cmd == "launch":
            idx = self._parse_index(args)
            if idx is not None and idx in self._instances:
                self._instances[idx].running = True
            logger.info(f"[MOCK] 启动实例 {idx}")
            return ""

        elif cmd == "quit":
            idx = self._parse_index(args)
            if idx is not None and idx in self._instances:
                self._instances[idx].running = False
            logger.info(f"[MOCK] 关闭实例 {idx}")
            return ""

        elif cmd == "adb":
            logger.info(f"[MOCK] ADB 命令: {args}")
            return ""

        logger.info(f"[MOCK] 未知命令: {args}")
        return ""

    def _parse_index(self, args: tuple) -> Optional[int]:
        """从命令参数中解析 --index N"""
        for i, arg in enumerate(args):
            if arg == "--index" and i + 1 < len(args):
                try:
                    return int(args[i + 1])
                except ValueError:
                    pass
        return None

    async def list_instances(self) -> list[LDInstance]:
        """列出所有模拟器实例"""
        output = await self._run_cmd("list2")
        instances = []

        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) >= 6:
                idx = int(parts[0])
                name = parts[1]
                running = parts[4] == "1"
                pid = int(parts[5]) if parts[5].isdigit() else 0
                # ADB 端口规则: 5555 + index * 2（雷电默认）
                adb_port = 5555 + idx * 2

                inst = LDInstance(
                    index=idx,
                    name=name,
                    adb_port=adb_port,
                    pid=pid,
                    running=running,
                )
                instances.append(inst)
                self._instances[idx] = inst

        return instances

    async def launch(self, index: int):
        """启动指定实例"""
        logger.info(f"启动模拟器实例 {index}")
        await self._run_cmd("launch", "--index", str(index))
        # 等待实例启动
        if not self.mock:
            await asyncio.sleep(3)

    async def quit(self, index: int):
        """关闭指定实例"""
        logger.info(f"关闭模拟器实例 {index}")
        await self._run_cmd("quit", "--index", str(index))

    async def quit_all(self):
        """关闭所有实例"""
        logger.info("关闭所有模拟器实例")
        await self._run_cmd("quitall")

    async def is_running(self, index: int) -> bool:
        """检查实例是否在运行"""
        instances = await self.list_instances()
        for inst in instances:
            if inst.index == index:
                return inst.running
        return False

    async def launch_app(self, index: int, package: str):
        """在指定实例中启动应用"""
        logger.info(f"实例 {index} 启动应用: {package}")
        await self._run_cmd("adb", "--index", str(index), "--command",
                            f"shell am start -n {package}")

    async def kill_app(self, index: int, package: str):
        """在指定实例中强制关闭应用"""
        logger.info(f"实例 {index} 关闭应用: {package}")
        await self._run_cmd("adb", "--index", str(index), "--command",
                            f"shell am force-stop {package}")

    async def set_network(self, index: int, enable: bool):
        """控制实例网络（断网/恢复）"""
        state = "enable" if enable else "disable"
        logger.info(f"实例 {index} 网络: {state}")
        # 通过 ADB shell 控制 wifi 和移动数据
        await self._run_cmd("adb", "--index", str(index), "--command",
                            f"shell svc wifi {state}")
        await self._run_cmd("adb", "--index", str(index), "--command",
                            f"shell svc data {state}")

    async def get_adb_serial(self, index: int) -> str:
        """获取实例的 ADB 连接地址 (host:port)"""
        inst = self._instances.get(index)
        if inst:
            return f"127.0.0.1:{inst.adb_port}"
        # 刷新实例列表
        await self.list_instances()
        inst = self._instances.get(index)
        if inst:
            return f"127.0.0.1:{inst.adb_port}"
        return f"127.0.0.1:{5555 + index * 2}"

    async def ensure_running(self, index: int, timeout: int = 60):
        """确保实例已启动，未启动则启动并等待就绪"""
        if await self.is_running(index):
            logger.info(f"实例 {index} 已在运行")
            return

        await self.launch(index)

        # 轮询等待启动完成
        for _ in range(timeout // 2):
            if await self.is_running(index):
                logger.info(f"实例 {index} 启动完成")
                return
            await asyncio.sleep(2)

        raise TimeoutError(f"实例 {index} 启动超时 ({timeout}s)")
