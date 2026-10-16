r"""Параллельная индексация всех баз: разбивка на N групп по размеру DT2,
каждая группа — отдельный процесс в index_partN.db.

Использование:
  python indexer\parallel.py split          — построить группы (groups.json)
  python indexer\parallel.py run 1          — запустить группу 1 (в фоне)
"""
import argparse
import json
import subprocess
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CONSPLUS_HOME", str(Path(__file__).parent.parent)))
BASE = Path(os.environ.get("CONSPLUS_BASES", str(ROOT / "BASE")))
N_GROUPS = 3
WORKERS_PER_PROC = 4


def base_sizes():
    if not BASE.is_dir():
        raise FileNotFoundError(f"CONSPLUS_BASES не указывает на папку BASE: {BASE}")
    out = []
    for d in sorted(BASE.iterdir()):
        if d.is_dir() and (d / f"{d.name}.DT2").exists() and (d / f"{d.name}.DT1").exists():
            out.append((d.name, (d / f"{d.name}.DT2").stat().st_size))
    return out


def split_groups():
    sizes = base_sizes()
    if not sizes:
        raise RuntimeError(f"В {BASE} не найдены базы с файлами DT1 и DT2")
    groups = [[] for _ in range(N_GROUPS)]
    gsum = [0] * N_GROUPS
    for name, size in sorted(sizes, key=lambda x: -x[1]):
        g = gsum.index(min(gsum))
        groups[g].append(name)
        gsum[g] += size
    (ROOT / "groups.json").write_text(json.dumps(groups, indent=1), encoding="utf-8")
    for i, (g, s) in enumerate(zip(groups, gsum), 1):
        print(f"group {i}: {len(g)} bases, {s / 1e9:.1f} GB: {g[:8]}...")
    return groups


def run_group(idx: int):
    if idx is None or not 1 <= idx <= N_GROUPS:
        raise ValueError(f"Номер группы должен быть от 1 до {N_GROUPS}")
    groups = json.loads((ROOT / "groups.json").read_text(encoding="utf-8"))
    names = groups[idx - 1]
    db = ROOT / f"index_part{idx}.db"
    if db.exists():
        raise FileExistsError(f"Индекс уже существует: {db}. Для полной сборки нужна пустая папка индексов.")
    log = ROOT / f"index_part{idx}.log"
    with open(log, "w", encoding="utf-8") as lf:
        for i, name in enumerate(names, 1):
            print(f"[{i}/{len(names)}] {name}", flush=True)
            lf.write(f"[{i}/{len(names)}] {name}\n")
            lf.flush()
            t0 = __import__("time").time()
            result = subprocess.run(
                [sys.executable, str(ROOT / "indexer" / "index_fast.py"),
                 str(BASE / name), str(db), "--workers", str(WORKERS_PER_PROC)],
                stdout=lf, stderr=subprocess.STDOUT, cwd=str(ROOT))
            if result.returncode != 0:
                lf.write(f"    FAILED: {name}, exit code {result.returncode}\n")
                lf.flush()
                raise RuntimeError(f"Индексация базы {name} завершилась с кодом {result.returncode}")
            lf.write(f"    done in {__import__('time').time()-t0:.0f}s\n")
            lf.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["split", "run"])
    ap.add_argument("group", type=int, nargs="?")
    args = ap.parse_args()
    if args.cmd == "split":
        split_groups()
    else:
        run_group(args.group)


if __name__ == "__main__":
    main()
