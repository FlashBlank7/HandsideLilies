from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .database import Database


class Risk(StrEnum):
    READ = "read"
    LAUNCH = "launch"
    MUTATE = "mutate"
    DESTRUCTIVE = "destructive"


class PermissionMode(StrEnum):
    CAUTIOUS = "cautious"
    STANDARD = "standard"
    TRUSTED = "trusted"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    needs_confirmation: bool
    reason: str


class PermissionBroker:
    def __init__(self, database: Database) -> None:
        self.database = database

    @property
    def mode(self) -> PermissionMode:
        raw = self.database.get_setting("permission_mode", PermissionMode.STANDARD.value)
        try:
            return PermissionMode(raw)
        except ValueError:
            return PermissionMode.STANDARD

    def set_mode(self, mode: PermissionMode | str) -> None:
        self.database.set_setting("permission_mode", PermissionMode(mode).value)

    def check(self, component_id: str, action_id: str, risk: Risk, confirmed: bool = False) -> PermissionDecision:
        if confirmed:
            return PermissionDecision(True, False, "用户已确认")
        if risk is Risk.DESTRUCTIVE:
            return PermissionDecision(False, True, "破坏性动作始终需要确认")
        mode = self.mode
        if mode is PermissionMode.CAUTIOUS:
            return PermissionDecision(False, True, "谨慎模式要求逐项确认")
        if mode is PermissionMode.STANDARD:
            if risk in (Risk.READ, Risk.LAUNCH):
                return PermissionDecision(True, False, "标准模式允许读取和启动")
            return PermissionDecision(False, True, "标准模式要求确认写操作")
        allowlist = self.database.get_setting("trusted_allowlist", [])
        key = f"{component_id}.{action_id}"
        if risk in (Risk.READ, Risk.LAUNCH) or key in allowlist:
            return PermissionDecision(True, False, "信任模式白名单允许")
        return PermissionDecision(False, True, "动作不在信任白名单")
