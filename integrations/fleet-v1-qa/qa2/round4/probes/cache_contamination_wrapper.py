import importlib.util, runpy, sys
from pathlib import Path
mode, snap, probe, epoch = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
if mode in ("fcntl", "fcntl-partial"):
    import fcntl  # noqa: F401
    if mode == "fcntl-partial":
        import types
        sys.modules["fcntl"] = types.ModuleType("fcntl")
if mode == "daemon":
    def load(name, rel):
        path = snap / rel
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    load("serveros_runtime_daemon", "runtime/daemon.py")
sys.argv = [str(probe), str(snap), epoch]
runpy.run_path(str(probe), run_name="__main__")
