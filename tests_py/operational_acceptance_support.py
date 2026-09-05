"""Instrumentación de aceptación Windows: sólo fixtures, sin inferencia ni cambios operativos.

@author Rodrigo Mason
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from pathlib import Path

import psutil


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".writing")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def evidence(name: str, value: dict) -> None:
    destination = os.environ.get("AIDO_ACCEPTANCE_EVIDENCE")
    if destination:
        save(Path(destination) / f"{name}.json", value)


def wait_until(predicate, timeout: float = 15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError("Acceptance condition exceeded its bounded deadline")


def native_readback(managed) -> dict:
    import win32api
    import win32con
    import win32job

    job = managed.native_handle
    pids = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicProcessIdList)
    membership = []
    for pid in pids:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
        try:
            membership.append(
                {
                    "pid": pid,
                    "created": psutil.Process(pid).create_time(),
                    "inJob": bool(win32job.IsProcessInJob(handle, job)),
                    "priority": psutil.Process(pid).nice(),
                }
            )
        finally:
            handle.Close()
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)

    class CpuRate(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_uint32), ("rate", ctypes.c_uint32)]

    cpu = CpuRate()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel.QueryInformationJobObject
    query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    query.restype = ctypes.c_int
    if not query(int(job), 15, ctypes.byref(cpu), ctypes.sizeof(cpu), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "members": membership,
        "cpuFlags": cpu.flags,
        "cpuRate": cpu.rate,
        "memoryBytes": limits["JobMemoryLimit"],
        "processLimit": limits["BasicLimitInformation"]["ActiveProcessLimit"],
        "limitFlags": limits["BasicLimitInformation"]["LimitFlags"],
    }


def process_sample(db_path: Path) -> dict:
    process = psutil.Process()
    memory = process.memory_info()
    io = process.io_counters()
    return {
        "timestamp": time.time(),
        "hostCpuPercent": psutil.cpu_percent(),
        "hostAvailableBytes": psutil.virtual_memory().available,
        "rssBytes": memory.rss,
        "privateBytes": getattr(memory, "private", None),
        "handles": process.num_handles(),
        "threads": process.num_threads(),
        "connections": len(process.net_connections()),
        "descendants": len(process.children(recursive=True)),
        "hostProcessCount": len(psutil.pids()),
        "readBytes": io.read_bytes,
        "writeBytes": io.write_bytes,
        "walBytes": Path(str(db_path) + "-wal").stat().st_size if Path(str(db_path) + "-wal").exists() else 0,
        "dbBytes": db_path.stat().st_size,
        "evidenceBytes": sum(p.stat().st_size for p in db_path.parent.rglob("*") if p.is_file()),
        "diskFreeBytes": psutil.disk_usage(str(db_path.parent)).free,
    }


def identities_gone(members: list[dict]) -> bool:
    for member in members:
        try:
            if abs(psutil.Process(member["pid"]).create_time() - member["created"]) < 0.01:
                return False
        except psutil.NoSuchProcess:
            pass
    return True
