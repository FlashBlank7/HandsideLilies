"""User-selected work bookmarks. No automatic capture, launch or startup replay.

Window titles are only held as hashes, never persisted as observation text.
Native effects are restricted to a short-lived, one-use UI preview. A saved HWND
is not an application checkpoint: closed windows and changed identities skip.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .database import Database, utc_now
from .desktop_peek import DesktopWindowApi, _validate_identity
from .windows import placement_from_dict, placement_to_dict

DOCUMENT_EXTENSIONS = frozenset({".pdf", ".txt", ".md", ".csv", ".json", ".png", ".jpg", ".jpeg",
    ".webp", ".gif", ".bmp", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".rtf"})
MAX_WINDOWS = 24
MAX_RESOURCES = 12


def _text(value: Any, maximum: int, label: str, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"{label}格式不正确，最多 {maximum} 字")
    value = value.strip()
    if required and not value:
        raise ValueError(f"请填写{label}")
    return value


def _ids(values: Any, maximum: int) -> list[str]:
    if not isinstance(values, list) or len(values) > maximum or any(not isinstance(v, str) for v in values):
        raise ValueError("选择项目的格式或数量不正确")
    if len(set(values)) != len(values):
        raise ValueError("请勿重复选择同一项")
    return values


def resource(value: str) -> dict[str, Any]:
    """Explicit local documents/folders or ordinary web links, not shell inputs.

    Do not search directories, read contents, expand environment variables or
    follow a changed junction at execution. Windows shortcuts/macros/scripts
    belong to the separately permissioned application launcher, not bookmarks.
    """
    source = _text(value, 4096, "资料地址", True)
    parts = urlsplit(source)
    if parts.scheme in {"http", "https"}:
        if not parts.hostname or parts.username or parts.password or "\\" in source or any(c.isspace() for c in source):
            raise ValueError("请使用不含登录凭据的 HTTP/HTTPS 地址")
        _ = parts.port
        return {"source": source, "kind": "web", "label": parts.hostname, "fingerprint": {}}
    if parts.scheme == "file":
        if parts.netloc or parts.query or parts.fragment:
            raise ValueError("请使用本机文件路径")
        source = unquote(parts.path)
        if re.match(r"^/[A-Za-z]:/", source):
            source = source[1:]
    elif parts.scheme and not re.match(r"^[A-Za-z]:[\\/]", source):
        raise ValueError("只能保存本机文档、文件夹或网页，不支持命令和应用快捷方式")
    if source.startswith(("\\\\", "//")) or ":" in source[2:]:
        raise ValueError("不支持网络共享、设备路径或替代数据流")
    path = Path(source)
    if not path.is_absolute():
        raise ValueError("请选择完整的本机路径")
    resolved = path.resolve(strict=True)
    if str(resolved).startswith(("\\\\", "//")):
        raise ValueError("不支持网络共享路径")
    folder = resolved.is_dir()
    if not folder and (not resolved.is_file() or resolved.suffix.casefold() not in DOCUMENT_EXTENSIONS):
        raise ValueError("仅支持普通文档、图片和文件夹；脚本、程序、快捷方式请从应用库打开")
    stat = resolved.stat()
    fingerprint = {"device": stat.st_dev, "inode": stat.st_ino}
    if not folder:
        fingerprint.update(size=stat.st_size, modified=stat.st_mtime_ns)
    return {"source": str(resolved), "kind": "folder" if folder else "file", "label": resolved.name,
            "fingerprint": fingerprint}


def _strong_identity(value: dict[str, Any] | None) -> dict[str, Any]:
    identity = _validate_identity(value)
    if not identity.get("processStarted") or not identity.get("executableHash") or not identity.get("titleHash"):
        raise ValueError("无法可靠确认窗口身份")
    return identity


def _same_window(saved: dict[str, Any], current: dict[str, Any] | None) -> bool:
    try:
        actual = _strong_identity(current)
        return all(saved.get(key) == actual.get(key) for key in
            ("handle", "processId", "processStarted", "executableHash", "className", "titleHash"))
    except (ValueError, TypeError):
        return False


class WorkResumptionService:
    def __init__(self, database: Database, *, api: DesktopWindowApi, opener: Callable[[dict], bool],
                 layout: Callable[[], str], task_get: Callable[[str], dict | None], clock=time.monotonic,
                 should_continue: Callable[[], bool] = lambda: True,
                 window_label: Callable[[int], str] = lambda handle: ""):
        self.database, self.api, self.opener = database, api, opener
        self.layout, self.task_get, self.clock = layout, task_get, clock
        self.should_continue = should_continue
        self.window_label = window_label
        self._capture: dict[str, Any] | None = None
        self._preview: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def list(self, offset: int = 0) -> dict[str, Any]:
        with self.database.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM work_resumptions").fetchone()[0]
            offset = min(max(0, int(offset)), ((total - 1) // 20) * 20 if total else 0)
            rows = db.execute("""SELECT record_id,title,task_id,task_title,next_step,created_at,resumed_at
                FROM work_resumptions ORDER BY created_at DESC,record_id LIMIT 20 OFFSET ?""", (offset,)).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "offset": offset, "hasMore": offset + 20 < total}

    def capture(self) -> dict[str, Any]:
        """Only enumerate when the user opens the save sheet. Nothing moves."""
        with self._lock:
            self._capture = None
            candidates = []
            layout = self.layout()
            for handle in self.api.list_handles()[:512]:
                try:
                    if self.api.is_minimized(handle):
                        continue
                    identity = _strong_identity(self.api.identity(handle))
                    place = placement_to_dict(placement_from_dict(self.api.placement(handle)))
                    # A minimized window is deliberately excluded: a work bookmark
                    # should not later hide a window the user has since restored.
                    if self.api.is_minimized(handle) or place["showCmd"] not in {1, 3, 9}:
                        continue
                    candidates.append({"id": uuid.uuid4().hex, "identity": identity, "placement": place,
                        "label": identity["processName"] or "应用窗口", "state": "最大化" if place["showCmd"] == 3 else "普通窗口"})
                    if len(candidates) == MAX_WINDOWS:
                        break
                except (ValueError, TypeError, OSError):
                    continue
            capture = {"id": uuid.uuid4().hex, "windows": candidates, "layout": layout, "expires": self.clock() + 600}
            self._capture = capture
            return {"id": capture["id"], "windows": [{"id": row["id"], "state": row["state"],
                "label": self.window_label(row["identity"]["handle"])[:160] or row["label"]} for row in candidates]}

    def save(self, capture_id: str, *, title: str, next_step: str, task_id: str = "",
             sources: list[str] | None = None, window_ids: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            capture = self._capture
            if not capture or capture["id"] != capture_id:
                raise ValueError("这次窗口预览已过期，请重新打开“留住刚才”")
            title = _text(title, 120, "名称", True)
            step = _text(next_step, 2000, "下一步", True)
            task_id = _text(task_id, 128, "任务")
            selected = _ids([] if window_ids is None else window_ids, MAX_WINDOWS)
            if selected and self.clock() > capture["expires"]:
                raise ValueError("窗口预览已过期，请刷新窗口列表再勾选；填写的内容会保留")
            if any(key not in {row["id"] for row in capture["windows"]} for key in selected):
                raise ValueError("只能保存本次列出的窗口")
            windows = [copy.deepcopy(row) for row in capture["windows"] if row["id"] in selected]
            current_handles = set(self.api.list_handles()) if windows else set()
            for row in windows:
                handle = row["identity"]["handle"]
                if self.layout() != capture["layout"] or handle not in current_handles or not _same_window(row["identity"], self.api.identity(handle)):
                    raise ValueError("所选窗口或显示器已变化，请重新查看后保存")
                # Save positions at confirmation time, not stale sheet-open time.
                place = placement_to_dict(placement_from_dict(self.api.placement(handle)))
                if self.api.is_minimized(handle) or place["showCmd"] not in {1, 3, 9}:
                    raise ValueError("所选窗口已收起，请重新查看后保存")
                row["placement"] = place
            resources = [dict(resource(source), id=uuid.uuid4().hex) for source in _ids([] if sources is None else sources, MAX_RESOURCES)]
            if len({row["source"] for row in resources}) != len(resources):
                raise ValueError("资料重复，请只保留一份")
            task = self.task_get(task_id) if task_id else None
            if task_id and (not task or task.get("archived")):
                raise ValueError("这个任务已不存在或已归档")
            record_id, now = uuid.uuid4().hex, utc_now()
            payload = {"version": 1, "windows": windows, "resources": resources, "layout": capture["layout"]}
            with self.database.connect() as db:
                db.execute("INSERT INTO work_resumptions VALUES(?,?,?,?,?,?,?,NULL)",
                    (record_id, title, task_id, task["title"] if task else "", step, json.dumps(payload, ensure_ascii=False), now))
            self._capture = None
            return {"id": record_id, "title": title}

    def _window_reason(self, row: dict, layout: str) -> str:
        if not layout or layout != self.layout():
            return "显示器或缩放已变化，不恢复旧位置"
        handle = row["identity"]["handle"]
        if handle not in self.api.list_handles():
            return "已关闭、不可管理或不在当前虚拟桌面"
        if not _same_window(row["identity"], self.api.identity(handle)):
            return "窗口身份或标题已变化，保守跳过"
        return ""

    @staticmethod
    def _resource_reason(row: dict) -> str:
        try:
            actual = resource(row["source"])
            if actual["source"] != row["source"] or actual["fingerprint"] != row["fingerprint"]:
                return "文件已修改或替换，请重新选择资料"
            return ""
        except (ValueError, OSError):
            return "资料已移走、不可用或不支持打开"

    def preview(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            self._preview = None
            with self.database.connect() as db:
                row = db.execute("SELECT * FROM work_resumptions WHERE record_id=?", (record_id,)).fetchone()
            if not row:
                raise ValueError("这份记录已删除")
            record = dict(row)
            payload = json.loads(record.pop("payload_json"))
            visible_windows, visible_resources = [], []
            for window in payload["windows"]:
                reason = self._window_reason(window, payload["layout"])
                label = (self.window_label(window["identity"]["handle"])[:160] if not reason else "") or window["label"]
                visible_windows.append({"id": window["id"], "label": label, "state": window["state"], "reason": reason, "available": not reason})
            for item in payload["resources"]:
                note = ""
                try:
                    current = resource(item["source"])
                    if current["source"] != item["source"]:
                        reason = "路径指向了别处，请重新选择资料"
                    else:
                        reason = ""
                        if current["fingerprint"] != item["fingerprint"]:
                            note = "文件已有更新；选中后会打开当前版本"
                        # Confirmation is bound to what this preview showed,
                        # not a stale copy from when the bookmark was saved.
                        item["fingerprint"] = current["fingerprint"]
                except (ValueError, OSError):
                    reason = "资料已移走、不可用或不支持打开"
                visible_resources.append({"id": item["id"], "label": item["label"], "source": item["source"], "kind": item["kind"], "reason": reason, "note": note, "available": not reason})
            token = uuid.uuid4().hex
            self._preview = {"token": token, "record": record, "payload": payload, "expires": self.clock() + 120,
                "windows": visible_windows, "resources": visible_resources}
            task = self.task_get(record["task_id"]) if record["task_id"] else None
            return copy.deepcopy({**record, "token": token, "windows": visible_windows, "resources": visible_resources,
                "taskAvailable": bool(task and not task.get("archived") and task.get("status") == "open")})

    def confirm(self, token: str, *, window_ids: list[str], resource_ids: list[str]) -> dict[str, Any]:
        """An explicit UI click consumes the preview, even if effects partially fail."""
        with self._lock:
            preview = self._preview
            if not preview or preview["token"] != token or self.clock() > preview["expires"]:
                raise ValueError("预览已过期或已使用，请重新预览")
            selected_windows = _ids(window_ids, MAX_WINDOWS)
            selected_resources = _ids(resource_ids, MAX_RESOURCES)
            for keys, rows in ((selected_windows, preview["windows"]), (selected_resources, preview["resources"])):
                if any(key not in {r["id"] for r in rows if r["available"]} for key in keys):
                    raise ValueError("只能执行预览里可用且由你选中的项目")
            self._preview = None
            record_id = preview["record"]["record_id"]
            run_id = uuid.uuid4().hex
            with self.database.connect() as db:
                if not db.execute("SELECT 1 FROM work_resumptions WHERE record_id=?", (record_id,)).fetchone():
                    raise ValueError("记录已删除，不执行恢复")
                db.execute("INSERT INTO work_resumption_runs VALUES(?,?,?,NULL,NULL)", (run_id, record_id, utc_now()))
            audit = self.database.audit("ui", "work-resumption", "confirm", "mutate", "allow",
                {"recordId": record_id, "runId": run_id, "windowCount": len(selected_windows), "resourceCount": len(selected_resources)})
            results = []
            payload = preview["payload"]
            # Restore selected windows before opening documents (which might
            # create or alter a window). Never close, minimize or activate others.
            try:
                for kind, rows, chosen in (("window", payload["windows"], selected_windows), ("resource", payload["resources"], selected_resources)):
                    for item in rows:
                        if item["id"] not in chosen:
                            continue
                        try:
                            if not self.should_continue():
                                results.append({"id": item["id"], "status": "skipped", "reason": "程序正在退出，不再执行新的操作"})
                                continue
                            reason = self._window_reason(item, payload["layout"]) if kind == "window" else self._resource_reason(item)
                            if reason:
                                results.append({"id": item["id"], "status": "skipped", "reason": reason})
                                continue
                            ok = self.api.restore(item["identity"]["handle"], item["placement"]) if kind == "window" else self.opener(item)
                            results.append({"id": item["id"], "status": "requested" if ok else "failed",
                                "reason": "已交给系统；不保证软件内部状态" if ok else "系统未接受操作，可能需要更高权限"})
                        except Exception:
                            results.append({"id": item["id"], "status": "failed", "reason": "未能完成此项，其它项目不受影响"})
                outcome = {"runId": run_id, "items": results, "requested": sum(r["status"] == "requested" for r in results),
                    "skipped": sum(r["status"] == "skipped" for r in results), "failed": sum(r["status"] == "failed" for r in results)}
                with self.database.connect() as db:
                    db.execute("UPDATE work_resumption_runs SET finished_at=?,result_json=? WHERE run_id=?", (utc_now(), json.dumps(outcome, ensure_ascii=False), run_id))
                    db.execute("UPDATE work_resumptions SET resumed_at=? WHERE record_id=?", (utc_now(), record_id))
                self.database.complete_audit(audit, result=outcome)
                labels = {item["id"]: item["label"] for item in payload["windows"] + payload["resources"]}
                # Labels are only for this UI result; audit/run history keeps IDs
                # and outcomes, not duplicate copies of user-selected filenames.
                return {**outcome, "items": [{**item, "label": labels[item["id"]]} for item in results]}
            except Exception:
                # An unfinished run is deliberately never replayed at startup.
                self.database.complete_audit(audit, error="恢复中断；不会自动重放")
                raise

    def delete(self, record_id: str) -> None:
        with self._lock, self.database.connect() as db:
            db.execute("DELETE FROM work_resumptions WHERE record_id=?", (record_id,))
            if self._preview and self._preview["record"]["record_id"] == record_id:
                self._preview = None

    def cancel(self) -> None:
        with self._lock:
            self._capture = self._preview = None
