import json
import time
import urllib.parse
import urllib.request

B = "http://127.0.0.1:8741"
QUERIES = [
    "налог на имущество",
    "увольнение работника",
    "неустойка по договору",
    "возмещение ущерба",
    "государственная регистрация",
    "страхование автомобиля",
    "взыскание задолженности",
    "защита прав потребителей",
    "арбитражный процесс",
    "земельный участок кадастровая",
]


def get(u):
    with urllib.request.urlopen(u, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


bases = get(f"{B}/api/bases")
report = []
log = open(r"tests\per_base_progress.log", "w", encoding="utf-8")
t_all = time.time()
for bi, base in enumerate(bases, 1):
    stats = []
    for q in QUERIES:
        t0 = time.time()
        try:
            res = get(f"{B}/api/search?q={urllib.parse.quote(q)}&base={base}&per_page=3")
            dt = time.time() - t0
            items = res.get("items", [])
            hits = len(items)
            first = ""
            if items:
                it = items[0]
                first = f"{it.get('kind') or ''} N {it.get('number') or ''} от {it.get('date')}"
        except Exception as e:
            dt, hits, first = time.time() - t0, -1, f"ERR {e}"
        stats.append({"q": q, "time": round(dt, 2), "hits": hits, "first": first})
    empty = sum(1 for s in stats if s["hits"] == 0)
    slow = sum(1 for s in stats if s["time"] > 3)
    line = (f"[{bi}/{len(bases)}] {base}: empty={empty} slow={slow} "
            f"avg={sum(s['time'] for s in stats)/len(stats):.2f}s")
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()
    report.append({"base": base, "queries": stats})

json.dump(report, open(r"tests\per_base_report.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"DONE in {time.time()-t_all:.0f}s")
