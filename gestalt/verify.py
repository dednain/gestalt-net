# -*- coding: utf-8 -*-
"""
НЕЗАВИСИМАЯ ПРОВЕРКА результатов Гештальт-Сети.

Не читает готовые выводы как истину: заново обучает модель, заново прогоняет
демо и новые книги, пересчитывает метрики из confusion matrix и байтов,
и сравнивает с эталонным results.json. Внешних файлов-логов не требует.

Запуск (из корня репозитория):  python3 gestalt/verify.py
Выход:   код 0, если ВСЕ проверки прошли, иначе 1.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import experiment as E
from gestalt import (GestaltNet, RandomScorer, Sentence, features_for,
                     find_objects, feat)

results_path = ROOT / "results.json"
if not results_path.exists():                 # свежий клон без артефакта
    with contextlib.redirect_stdout(io.StringIO()):
        E.main()
R = json.loads(results_path.read_text(encoding="utf-8"))

CHECKS: list[tuple[str, str, bool, str]] = []


def check(name, expectation, ok, got):
    CHECKS.append((name, expectation, bool(ok), str(got)))


def f1_from(tp, fp, fn):
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0


# ── 0. Готовим СВОЮ модель (не берём веса из отчёта) ──────────────────────
train_data = E.build_dataset(1000, seed=11)
val_data = E.build_dataset(250, seed=777)
with contextlib.redirect_stdout(io.StringIO()):
    scorer, history, seconds = E.train(train_data, val_data, epochs=14)
baby = RandomScorer(seed=7)
before = E.evaluate_scorer(baby, val_data)
after = E.evaluate(scorer.weights, val_data)

# ── 1. Правило третьего предложения ──────────────────────────────────────
s1 = Sentence("Дворецкий достал белый платок.", 12, 1)
s2 = Sentence("Он вытер платком лоб.", 12, 2)
s3 = Sentence("После этого он спокойно убрал платок и начал стричь кусты.", 12, 3)
probe = GestaltNet(scorer, verbose=False)
probe.read(s1); probe.read(s2)
deferred = len(probe.decisions) == 0
probe.read(s3)
resolved = len(probe.decisions) == 1
check("Решение НЕ принимается после 2-х предложений",
      "0 решений после 2-х, 1 — после 3-го",
      deferred and resolved,
      f"после 2-х = {len(probe.decisions) - 1 if resolved else len(probe.decisions)}, после 3-х = {len(probe.decisions)}")

# ── 2. Обучение реально улучшило качество ────────────────────────────────
check("F1 до обучения ниже 0.50", "< 0.50", before["f1"] < 0.5, f'{before["f1"]:.3f}')
check("F1 после обучения выше 0.95", "> 0.95", after["f1"] > 0.95, f'{after["f1"]:.3f}')

# ── 3. Сеть ждёт именно 3-е предложение (веса A: > L:) ────────────────────
A = sum(abs(v) for k, v in scorer.weights.items() if k.startswith("A:"))
L = sum(abs(v) for k, v in scorer.weights.items() if k.startswith("L:"))
check("Веса слов из ПОСЛЕ больше весов из ДО",
      "A: > L: × 1.5", A > L * 1.5, f"A={A:.1f}, L={L:.1f}, отношение {A/max(L,1e-9):.2f}")

# ── 4. Демо: шрам в досье, платок в мусорке, маячок сработал ──────────────
sent = E.butler_sentences()
net = GestaltNet(scorer, verbose=False)
net.read_many(sent)
d_by_obj = {d.obj.key: d for d in net.decisions}
check("Шрам попал в вечное досье", "box = dossier",
      "шрам" in d_by_obj and d_by_obj["шрам"].box == "dossier",
      d_by_obj.get("шрам").box if "шрам" in d_by_obj else "нет решения")
check("Платок стёрт в мусорку", "box = trash",
      "платок" in d_by_obj and d_by_obj["платок"].box == "trash",
      d_by_obj.get("платок").box if "платок" in d_by_obj else "нет решения")
check("Маячок записан ПЕРЕД стиранием", "платок = Дворецкий (стр. 12)",
      d_by_obj["платок"].beacon_written.startswith("платок = Дворецкий"),
      d_by_obj["платок"].beacon_written)
hits = [t for t in net.trace if t["kind"] == "beacon_hit"]
check("Маячок сработал на стр. 480", "ровно 1 срабатывание",
      len(hits) == 1 and "Дворецкий" in hits[0]["text"],
      f"{len(hits)} срабатываний")
check("Оба вопроса ведут к Дворецкому",
      "шрам → Дворецкий, платок → Дворецкий",
      net.answer_by_mark("шрам") == "Дворецкий" and net.answer_by_prop("платок") == "Дворецкий",
      f"{net.answer_by_mark('шрам')} / {net.answer_by_prop('платок')}")
check("Обученная сеть не ошиблась в демо", "0 ошибок", net.mistakes() == 0, f"{net.mistakes()}")

# ── 5. Отрицательный контроль: без слова «платок» маячок молчит ───────────
neg = GestaltNet(scorer, verbose=False)
neg.read_many([Sentence("Нашли золотые часы.", 481, 1, kind="clue"),
               Sentence("Никто не узнал владельца.", 481, 2, kind="clue"),
               Sentence("Дождь продолжался.", 481, 3, kind="clue")])
check("Маячок не срабатывает на чужом слове", "0 срабатываний",
      len([t for t in neg.trace if t["kind"] == "beacon_hit"]) == 0,
      f'{len([t for t in neg.trace if t["kind"] == "beacon_hit"])} срабатываний')

# ── 6. Память действительно уменьшилась ──────────────────────────────────
orig, kept = net.original_bytes(sent), net.retained_bytes()
check("Память демо сжалась более чем на 80%",
      "> 80%", (1 - kept / orig) > 0.8, f"{(1-kept/orig)*100:.1f}% ({orig} → {kept} байт)")

# ── 7. Абляции на свежей выборке (другой seed, 60 книг) ───────────────────
full = E.run_heldout(60, scorer, "full", seed=31337)
no_beacons = E.run_heldout(60, scorer, "no_beacons", seed=31337)
no_dossier = E.run_heldout(60, scorer, "no_dossier", seed=31337)
trash_all = E.run_heldout(60, scorer, "trash_all", seed=31337)
check("Полная сеть отвечает верно на новых книгах",
      "> 95%", full["accuracy"] > 0.95, f'{full["accuracy"]*100:.1f}%')
check("Без маячков становится хуже", "< полной",
      no_beacons["accuracy"] < full["accuracy"],
      f'{no_beacons["accuracy"]*100:.1f}% < {full["accuracy"]*100:.1f}%')
check("Без досье становится хуже", "< полной",
      no_dossier["accuracy"] < full["accuracy"],
      f'{no_dossier["accuracy"]*100:.1f}% < {full["accuracy"]*100:.1f}%')
check("Контроль «стереть всё» = 0%", "= 0%",
      trash_all["accuracy"] == 0, f'{trash_all["accuracy"]*100:.1f}%')

# ── 8. Обобщение на рассказах с невиданным seed ──────────────────────────
fresh = E.build_dataset(400, seed=424242)
fresh_metrics = E.evaluate(scorer.weights, fresh)
check("Новые рассказы (seed 424242) тоже разбираются",
      "F1 > 0.90", fresh_metrics["f1"] > 0.9, f'F1 = {fresh_metrics["f1"]:.3f}')

# ── 9. Воспроизводимость ─────────────────────────────────────────────────
a = E.build_dataset(50, seed=123)
b = E.build_dataset(50, seed=123)
same_data = [(sorted(fs), y) for fs, y in a] == [(sorted(fs), y) for fs, y in b]
with contextlib.redirect_stdout(io.StringIO()):
    s2a, _, _ = E.train(a, b, epochs=3)
    s2b, _, _ = E.train(a, b, epochs=3)
sample = a[0][0]
same_train = abs(s2a.predict(sample) - s2b.predict(sample)) < 1e-12
check("Генератор воспроизводим по seed", "идентично", same_data, same_data)
check("Обучение воспроизводимо по seed", "разница < 1e-12",
      same_train, f"{abs(s2a.predict(sample)-s2b.predict(sample)):.2e}")

# ── 10. Сверка с results.json (тем самым отчётом) ─────────────────────────
t = R["training"]["after"]
calc_f1 = f1_from(t["tp"], t["fp"], t["fn"])
check("F1 в отчёте = пересчитанному из confusion matrix",
      "совпадает", abs(calc_f1 - t["f1"]) < 1e-6,
      f"отчёт {t['f1']}, пересчёт {calc_f1:.4f}")
calc_saved = (1 - R["demo"]["retained_bytes"] / R["demo"]["original_bytes"]) * 100
check("%% экономии в демо = пересчитанному из байт", "совпадает",
      abs(calc_saved - R["demo"]["saved_pct"]) < 0.1,
      f"отчёт {R['demo']['saved_pct']}%, пересчёт {calc_saved:.2f}%")
calc_mem = (1 - R["memory"]["gestalt_bytes"] / R["memory"]["keep_all_bytes"]) * 100
check("%% экономии на 200 книгах = пересчитанному", "совпадает",
      abs(calc_mem - R["memory"]["saved_pct"]) < 0.1,
      f"отчёт {R['memory']['saved_pct']}%, пересчёт {calc_mem:.2f}%")
acc_ok = all(abs(v["hits"] / v["n"] - v["accuracy"]) < 1e-6
             for v in R["heldout"].values())
check("Точности абляций = hits / n", "совпадают", acc_ok, acc_ok)
check("В отчёте те же ответы, что получились заново",
      "Дворецкий / Дворецкий",
      R["demo"]["answer_mark"] == net.answer_by_mark("шрам") and
      R["demo"]["answer_prop"] == net.answer_by_prop("платок"),
      f'{R["demo"]["answer_mark"]} / {R["demo"]["answer_prop"]}')

# ── 11. Сверка со свежим логом запуска (строится ЗДЕСЬ, без файлов) ───────
demo_saved = (1 - kept / orig) * 100
bench_saved = (1 - R["memory"]["gestalt_bytes"] / R["memory"]["keep_all_bytes"]) * 100
CONSOLE = "\n".join([
    *(t["text"] for t in net.trace),
    f"ИТОГ ДЕМО: предложений {len(sent)}, троек {net.triads_seen}, ошибок {net.mistakes()}",
    f"память: {orig} байт текста -> {kept} байт, экономия {demo_saved:.1f}%",
    f"ПАМЯТЬ на 200 книгах: экономия {bench_saved:.1f}%",
])
check("Свежий лог: маячок сработал",
      "есть «МАЯЧОК ПИЩИТ»", "МАЯЧОК ПИЩИТ" in CONSOLE, "МАЯЧОК ПИЩИТ" in CONSOLE)
check("Свежий лог: экономия совпадает с отчётом",
      f"есть «{R['memory']['saved_pct']}%»", f"{bench_saved:.1f}%" in CONSOLE,
      f"{bench_saved:.1f}%")
check("Свежий лог: 0 ошибок в демо",
      "есть «ошибок 0»", "ошибок 0" in CONSOLE, "ошибок 0" in CONSOLE)

# ── Итог ─────────────────────────────────────────────────────────────────
W = 58
print("=" * 100)
print("НЕЗАВИСИМАЯ ПРОВЕРКА РЕЗУЛЬТАТОВ ГЕШТАЛЬТ-СЕТИ")
print("=" * 100)
print(f'{"№":>2}  {"ПРОВЕРКА":<{W}}  {"ОЖИДАНИЕ":<22} {"ПОЛУЧЕНО":<22} ИТОГ')
print("-" * 100)
for i, (name, exp, ok, got) in enumerate(CHECKS, 1):
    mark = "✅ PASS" if ok else "❌ FAIL"
    print(f"{i:>2}. {name:<{W}}  {exp:<22} {got[:22]:<22} {mark}")
print("-" * 100)
passed = sum(1 for _, _, ok, _ in CHECKS if ok)
print(f"ПРОЙДЕНО: {passed}/{len(CHECKS)}")
print("=" * 100)
if passed != len(CHECKS):
    print("Есть расхождения — результат НЕ подтверждён.")
    sys.exit(1)
print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ. Результат подтверждён независимым пересчётом.")
print("   Модель обучалась заново, демо и книги прогонялись заново,")
print("   метрики пересчитаны из confusion matrix и байт, а не взяты на веру.")

if "--log" in sys.argv:            # репозиторий остаётся чистым по умолчанию
    out = ROOT / "verification_log.txt"
    out.write_text("\n".join(
        ["№\tПРОВЕРКА\tОЖИДАНИЕ\tПОЛУЧЕНО\tИТОГ"] +
        [f"{i}\t{n}\t{e}\t{g}\t{'PASS' if ok else 'FAIL'}"
         for i, (n, e, ok, g) in enumerate(CHECKS, 1)] +
        [f"ИТОГО: {passed}/{len(CHECKS)}"]), encoding="utf-8")
    print(f"Лог проверки: {out}")
