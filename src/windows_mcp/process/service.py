from typing import Literal


__all__ = ["list_processes", "kill_process"]


def list_processes(
    name: str | None = None,
    sort_by: Literal["memory", "cpu", "name"] = "memory",
    limit: int = 20,
) -> str:
    import psutil
    from tabulate import tabulate

    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        try:
            info = p.info
            mem_mb = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info["name"] or "Unknown",
                    "cpu": info["cpu_percent"] or 0,
                    "mem_mb": round(mem_mb, 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if name:
        from thefuzz import fuzz

        procs = [p for p in procs if fuzz.partial_ratio(name.lower(), p["name"].lower()) > 60]
    sort_key = {
        "memory": lambda x: x["mem_mb"],
        "cpu": lambda x: x["cpu"],
        "name": lambda x: x["name"].lower(),
    }
    procs.sort(key=sort_key.get(sort_by, sort_key["memory"]), reverse=(sort_by != "name"))
    procs = procs[:limit]
    if not procs:
        return f"No processes found{f' matching {name}' if name else ''}."
    table = tabulate(
        [[p["pid"], p["name"], f"{p['cpu']:.1f}%", f"{p['mem_mb']:.1f} MB"] for p in procs],
        headers=["PID", "Name", "CPU%", "Memory"],
        tablefmt="simple",
    )
    return f"Processes ({len(procs)} shown):\n{table}"


# Killing any of these crashes or logs off Windows (or, for the last entries, the session UI).
PROTECTED_PROCESSES = frozenset(
    {
        "system", "system idle process", "idle", "registry", "memory compression", "secure system",
        "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
        "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "sihost.exe",
    }
)  # fmt: skip


def _protected_reason(pid: int, name: str) -> str | None:
    import os

    import psutil

    if name.lower() in PROTECTED_PROCESSES:
        return f"{name} is a critical Windows process"
    if pid == os.getpid():
        return "that is this Windows-MCP server itself"
    try:
        if pid == psutil.Process(os.getpid()).ppid():
            return "that is the MCP client that runs this server"
    except psutil.Error:
        pass
    return None


def kill_process(
    name: str | None = None, pid: int | None = None, force: bool = False
) -> str:
    import psutil

    if pid is None and name is None:
        return "Error: Provide either pid or name parameter for kill mode."
    if name is not None and name.lower() in PROTECTED_PROCESSES:
        raise PermissionError(f"Refusing to kill {name}: it is a critical Windows process.")
    killed = []
    if pid is not None:
        try:
            p = psutil.Process(pid)
            pname = p.name()
            reason = _protected_reason(pid, pname)
            if reason:
                raise PermissionError(f"Refusing to kill PID {pid}: {reason}.")
            if force:
                p.kill()
            else:
                p.terminate()
            killed.append(f"{pname} (PID {pid})")
        except psutil.NoSuchProcess:
            return f"No process with PID {pid} found."
        except psutil.AccessDenied:
            return f"Access denied to kill PID {pid}. Try running as administrator."
    else:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["name"] and p.info["name"].lower() == name.lower():
                    if _protected_reason(p.info["pid"], p.info["name"]):
                        continue  # never take down this server or its MCP client by name
                    if force:
                        p.kill()
                    else:
                        p.terminate()
                    killed.append(f"{p.info['name']} (PID {p.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    if not killed:
        return f'No process matching "{name}" found or access denied.'
    return f"{'Force killed' if force else 'Terminated'}: {', '.join(killed)}"
