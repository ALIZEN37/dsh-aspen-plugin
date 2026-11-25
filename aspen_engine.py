# -*- coding: utf-8 -*-
"""Aspen Plus COM 驱动层（Windows + pywin32）。

设计目标：给 MCP 服务器提供一组稳定的、可 JSON 化的 Aspen 操作原语。
- 惰性单例：同一进程只维护一个 Apwn.Document。
- 所有 COM 异常统一翻译成 AspenError(code, message, next_actions)。
- 返回值全部 JSON 友好（float/str/bool/None/dict/list）。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEPS = os.path.join(HERE, "deps")


def _prepare_deps() -> None:
    """把随包携带的 pywin32 目录加入 sys.path 和 DLL 搜索路径。"""
    for p in (
        DEPS,
        os.path.join(DEPS, "win32"),
        os.path.join(DEPS, "win32", "lib"),
        os.path.join(DEPS, "pywin32_system32"),
    ):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    dll = os.path.join(DEPS, "pywin32_system32")
    if os.path.isdir(dll):
        try:
            os.add_dll_directory(dll)
        except Exception:
            pass


_prepare_deps()

try:
    import win32com.client  # type: ignore
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    win32com = None  # type: ignore
    _IMPORT_ERROR = exc

ASPEN_CANDIDATES = [
    r"D:\aspen\V14.2\Aspen Plus V14.0\GUI\Xeq\aspenplus.exe",
    r"C:\Program Files\AspenTech\Aspen Plus V14.0\GUI\Xeq\aspenplus.exe",
    r"C:\Program Files\AspenTech\Aspen Plus V12\GUI\Xeq\aspenplus.exe",
    r"C:\Program Files (x86)\AspenTech\Aspen Plus V12\GUI\Xeq\aspenplus.exe",
]


class AspenError(Exception):
    """带错误码与恢复建议的业务异常。"""

    def __init__(self, code: str, message: str, next_actions=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_actions = list(next_actions or [])

    def to_dict(self) -> dict:
        return {
            "error_code": self.code,
            "message": self.message,
            "next_actions": self.next_actions,
        }


def _jsonable(value):
    """把 COM 返回值转成 JSON 可序列化的 Python 值。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        import decimal

        if isinstance(value, decimal.Decimal):
            return float(value)
    except Exception:
        pass
    # Variant / 数组 / COM 对象
    try:
        return float(value)
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return repr(value)


def find_aspen() -> dict:
    """定位 aspenplus.exe：先查常见路径，再查注册表。"""
    for path in ASPEN_CANDIDATES:
        if os.path.exists(path):
            return {"found": True, "exe": path, "source": "candidate"}
    try:
        import winreg  # type: ignore

        keys = [
            (r"SOFTWARE\WOW6432Node\AspenTech\Aspen Plus", "InstallDir"),
            (r"SOFTWARE\AspenTech\Aspen Plus", "InstallDir"),
        ]
        for root in (winreg.HKEY_LOCAL_MACHINE,):
            for sub, name in keys:
                try:
                    with winreg.OpenKey(root, sub) as k:
                        val, _ = winreg.QueryValueEx(k, name)
                        exe = os.path.join(val, "GUI", "Xeq", "aspenplus.exe")
                        if os.path.exists(exe):
                            return {"found": True, "exe": exe, "source": "registry"}
                except Exception:
                    continue
    except Exception:
        pass
    # 兜底：扫描 D:\aspen 一层
    for root in (r"D:\aspen", r"C:\Program Files\AspenTech"):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            exe = os.path.join(root, name, "GUI", "Xeq", "aspenplus.exe")
            if os.path.exists(exe):
                return {"found": True, "exe": exe, "source": "scan"}
    return {"found": False, "exe": None, "source": "none"}


class AspenEngine:
    """一个进程内共享的 Aspen 文档会话。"""

    def __init__(self) -> None:
        self.doc = None
        self.case_path = None
        self.opened_at = None
        self.last_run = None
        self.last_error = None

    # ---------- 内部辅助 ----------

    def _require_doc(self):
        if self.doc is None:
            raise AspenError(
                "no_case_open",
                "当前没有打开的 Aspen 案例。",
                ["先调用 aspen_open_case 打开一个 .bkp/.inp 案例"],
            )
        return self.doc

    def _ensure_com(self):
        if win32com is None:
            raise AspenError(
                "missing_dependency",
                "pywin32 不可用：%s" % (_IMPORT_ERROR,),
                ["检查插件目录下 deps/ 是否完整（win32com、pythoncom）"],
            )

    def _dispatch(self):
        self._ensure_com()
        try:
            return win32com.client.Dispatch("Apwn.Document")
        except Exception as exc:
            msg = str(exc)
            hints = [
                "确认 Aspen Plus 已安装且 COM 已注册（regsvr32 / Aspen 安装程序修复）",
                "确认 Sentinel RMS License Manager 服务正在运行",
                "若在 DSH 沙箱内运行，Aspen 会在启动阶段崩溃：需要全盘访问（danger-full-access）或让 MCP 进程不受工作区沙箱限制",
            ]
            raise AspenError("aspen_launch_failed", "创建 Apwn.Document 失败：%s" % msg, hints)

    def _iter_elements(self, node):
        """稳健地把 Tree node 的子节点迭代出来（COM 集合两种访问方式都试）。"""
        els = node.Elements
        out = []
        try:
            for child in els:
                out.append(child)
            return out
        except Exception:
            pass
        try:
            count = int(els.Count)
        except Exception:
            count = 0
        for i in range(count):
            child = None
            for accessor in ("Item", None):
                try:
                    if accessor == "Item":
                        child = els.Item(i)
                    else:
                        child = els[i]
                    break
                except Exception:
                    child = None
            if child is not None:
                out.append(child)
        return out

    @staticmethod
    def _node_name(child) -> str:
        try:
            return str(child.Name)
        except Exception:
            return ""

    # ---------- 状态 ----------

    def status(self) -> dict:
        exe = find_aspen()
        return {
            "doc_open": self.doc is not None,
            "case_path": self.case_path,
            "opened_s": round(time.time() - self.opened_at, 1) if self.opened_at else None,
            "last_run": self.last_run,
            "last_error": self.last_error,
            "aspen": exe,
            "python": sys.version.split()[0],
            "pywin32_available": win32com is not None,
            "server_deps": DEPS,
        }

    # ---------- 案例生命周期 ----------

    def open_case(self, path: str, run: bool = False) -> dict:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(path):
            raise AspenError(
                "case_not_found",
                "案例文件不存在：%s" % path,
                ["用绝对路径，并确认文件是 .bkp 或 .inp"],
            )
        if self.doc is not None:
            self.close(save=False)

        t0 = time.time()
        doc = self._dispatch()
        try:
            doc.SuppressDialogs = 1
        except Exception:
            pass
        try:
            doc.Visible = 0
        except Exception:
            pass

        ext = os.path.splitext(path)[1].lower()
        methods = ["InitFromArchive2"] if ext in (".bkp", ".apw") else ["InitFromFile2", "InitFromArchive2", "InitFromFile"]
        errors = []
        loaded = False
        for method in methods:
            fn = getattr(doc, method, None)
            if fn is None:
                errors.append("%s: 方法不存在" % method)
                continue
            try:
                fn(path)
                loaded = True
                break
            except Exception as exc:
                errors.append("%s: %s" % (method, exc))
        if not loaded:
            try:
                doc.Close()
            except Exception:
                pass
            raise AspenError(
                "case_load_failed",
                "加载案例失败：%s" % path,
                errors + ["确认该 .bkp 能在本机 Aspen 中手动打开"],
            )

        self.doc = doc
        self.case_path = path
        self.opened_at = time.time()
        result = {
            "case": path,
            "loaded": True,
            "load_s": round(time.time() - t0, 2),
            "streams": self.list_streams(),
            "blocks": self.list_blocks(),
        }
        if run:
            result["run"] = self.run()
        return result

    def run(self, max_wait_s: float = 900.0) -> dict:
        doc = self._require_doc()
        t0 = time.time()
        try:
            doc.Engine.Run2()
        except Exception as exc:
            info = {"elapsed_s": round(time.time() - t0, 2), "ok": False, "message": str(exc)}
            self.last_error = info
            raise AspenError(
                "run_failed",
                "运行失败：%s" % exc,
                ["检查输入是否完整（组分/物性方法/进料）", "手动在 Aspen 中运行一次看报错"],
            )
        elapsed = round(time.time() - t0, 2)
        per_error = None
        try:
            per_error = _jsonable(self.get_raw(r"\Data\Results Summary\Run-Status\Output\PER_ERROR"))
        except Exception:
            pass
        info = {
            "ok": per_error in (0, 0.0, None),
            "elapsed_s": elapsed,
            "per_error": per_error,
            "converged": per_error in (0, 0.0),
        }
        self.last_run = info
        return info

    def save(self, path: str | None = None) -> dict:
        doc = self._require_doc()
        target = os.path.abspath(os.path.expanduser(path)) if path else self.case_path
        if path:
            save_as = getattr(doc, "SaveAs", None)
            if save_as is None:
                raise AspenError(
                    "save_as_unsupported",
                    "当前 Aspen COM 接口不支持 SaveAs。",
                    ["用 aspen_save 不带路径保存回原案例，或用 aspen_export_report 导出报告"],
                )
            save_as(target)
        else:
            doc.Save()
        return {"saved": True, "path": target}

    def close(self, save: bool = False) -> dict:
        doc = self.doc
        self.doc = None
        self.case_path = None
        self.opened_at = None
        if doc is None:
            return {"closed": False, "reason": "no_case_open"}
        try:
            if save:
                doc.Save()
            doc.Close()
            return {"closed": True, "saved": bool(save)}
        except Exception as exc:
            return {"closed": False, "reason": str(exc)}

    # ---------- 树读取 / 写入 ----------

    def get_raw(self, path: str):
        doc = self._require_doc()
        try:
            node = doc.Tree.FindNode(path)
        except Exception as exc:
            raise AspenError("bad_tree_path", "无效的树路径：%s（%s）" % (path, exc))
        if node is None:
            raise AspenError("node_not_found", "节点不存在：%s" % path, ["先用 aspen_list 查看该节点的子节点名"])
        return node.Value

    def get(self, paths) -> dict:
        if isinstance(paths, str):
            paths = [paths]
        out = []
        for path in paths:
            try:
                value = _jsonable(self.get_raw(path))
                out.append({"path": path, "found": True, "value": value})
            except AspenError as exc:
                out.append({"path": path, "found": False, "error": exc.to_dict()})
        return {"count": len(out), "values": out}

    def set(self, path: str, value) -> dict:
        doc = self._require_doc()
        try:
            node = doc.Tree.FindNode(path)
        except Exception as exc:
            raise AspenError("bad_tree_path", "无效的树路径：%s（%s）" % (path, exc))
        if node is None:
            raise AspenError("node_not_found", "节点不存在：%s" % path)
        try:
            old = _jsonable(node.Value)
        except Exception:
            old = None
        try:
            node.Value = value
        except Exception as exc:
            raise AspenError(
                "set_failed",
                "写入失败：%s（%s）" % (path, exc),
                ["确认节点是 Input 分支下的可写输入", "检查单位与类型是否匹配"],
            )
        new = None
        try:
            new = _jsonable(node.Value)
        except Exception:
            pass
        return {"path": path, "old": old, "new": new, "written": True}

    def list_children(self, path: str = r"\Data\Streams", with_values: bool = False, limit: int = 300) -> dict:
        doc = self._require_doc()
        try:
            node = doc.Tree.FindNode(path)
        except Exception as exc:
            raise AspenError("bad_tree_path", "无效的树路径：%s（%s）" % (path, exc))
        if node is None:
            raise AspenError("node_not_found", "节点不存在：%s" % path)
        children = self._iter_elements(node)
        names = []
        for child in children[:limit]:
            entry = {"name": self._node_name(child)}
            if with_values:
                try:
                    entry["value"] = _jsonable(child.Value)
                except Exception:
                    entry["value"] = None
            names.append(entry)
        return {"path": path, "count": len(children), "children": names}

    def list_streams(self) -> list:
        try:
            return [c["name"] for c in self.list_children(r"\Data\Streams")["children"]]
        except Exception:
            return []

    def list_blocks(self) -> list:
        try:
            return [c["name"] for c in self.list_children(r"\Data\Blocks")["children"]]
        except Exception:
            return []
