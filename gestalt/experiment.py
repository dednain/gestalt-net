# -*- coding: utf-8 -*-
"""
Эксперимент: от «младенца» до работающей Гештальт-Сети.

  Этап 2. 1000 учебных рассказов-загадок.
  Этап 3. Обучение методом проб и ошибок (SGD по логистической потере).
  Этап 4. Проверка боем на 200 новых книгах + замер памяти.
  Демо.   Тот самый Дворецкий: платок, чашка, шрам, золотые часы, маячок.

Метрика обучения — НЕ обычная точность: «улик» всего ~11%, поэтому сеть,
которая всё выбрасывает, уже имеет 88% точности. Используем сбалансированную
точность (среднее полноты по уликам и по мусору) и F1 по классу «улика».

Запуск (из корня репозитория):  python3 gestalt/experiment.py
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

# Позволяет запускать и как скрипт, и как модуль из корня репозитория.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gestalt import (GestaltNet, RandomScorer, TrainedScorer, Sentence, Triad,
                     CHARS, OBJECTS, find_chars, features_for, feat, tokens, sigmoid)

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "results.json"

CHAR_NAMES = [c[0] for c in CHARS]
OBJ_BY_KEY = {c.key: c for c in OBJECTS}

# ---------------------------------------------------------------------------
# Фразовые заготовки для учебных рассказов
# ---------------------------------------------------------------------------

MARKS = [("шрам-полумесяц на запястье", "шрам"),
         ("татуировку на шее", "татуировка"),
         ("старую хромоту", "хромота"),
         ("родинку на щеке", "родинка"),
         ("седину на висках", "седина")]

PROPS = [("белый платок", "платок"), ("фарфоровую чашку", "чашка"),
         ("оловянную ложку", "ложка"), ("вчерашнюю газету", "газета"),
         ("сломанный зонт", "зонт"), ("кожаную перчатку", "перчатка"),
         ("ржавый ключ", "ключ"), ("незапечатанное письмо", "письмо"),
         ("пустой стакан", "стакан"), ("глиняную тарелку", "тарелка"),
         ("медную пуговицу", "пуговица"), ("старый кошелек", "кошелек"),
         ("карманный фонарь", "фонарь"), ("жесткую щетку", "щетка")]

LUX = [("золотые часы", "золотые часы"),
       ("жемчужный медальон", "медальон"),
       ("серебряное кольцо", "кольцо")]

LEFT = ["На странице {p} в комнату вошел {c}.",
        "Утро было пасмурным, и {c} стоял у окна.",
        "В коридоре показался {c}.",
        "{c} молча вошел и остановился у двери."]

LEFT_POOR = ["Бедняк {c} в латаном пальто вошел в дом.",
             "Нищий {c} в дырявом сюртуке остановился у порога.",
             "Обноски {c} давно видели и не в первый раз."]

MID_MARK = ["{c} нечаянно показал {o}.",
            "{c} поправил воротник, и стал виден {o}.",
            "{c} открыто не прятал {o}."]

MID_PROP = ["{c} достал {o}.",
            "{c} держал в руках {o}.",
            "{c} поднял с пола {o}.",
            "{c} вытер {o} и осмотрелся.",
            "{c} поставил {o} на стол."]

MID_LUX = ["{c} поспешно прикрыл {o} ладонью.",
           "В кармане {c} блеснул {o}."]

AFTER_CLOSED = ["После этого {c} спокойно убрал вещь и вернулся к своим делам.",
                "{c} равнодушно продолжил работу, не поднимая головы.",
                "Ничего не изменилось: {c} принялся за обычные дела.",
                "{c} отложил находку и спокойно продолжил уборку."]

AFTER_OPEN = ["{c} испуганно оглянулся и убежал, ничего не объяснив.",
              "Голос {c} дрогнул, и он торопливо скрылся в темноте.",
              "{c} замер, покраснел и отвел глаза.",
              "{c} внезапно зашептал, оглядываясь по сторонам."]

# вид тройки -> должна ли улика остаться в вечном досье (метка учителя)
LABEL = {"closed_prop": 0, "open_prop": 1, "mark_closed": 1,
         "mark_open": 1, "anomaly": 1}


def make_triad(rng: random.Random, page: int, kind: str,
               char: str | None = None, forced=None, idx0: int = 0):
    """Собрать одну тройку ДО -> СЕЙЧАС -> ПОСЛЕ и вернуть её с меткой."""
    char = char or rng.choice(CHAR_NAMES)

    if kind == "anomaly":
        phrase, key = forced or rng.choice(LUX)
        left = rng.choice(LEFT_POOR).format(c=char)
        mid = rng.choice(MID_LUX).format(c=char, o=phrase)
        after = rng.choice(AFTER_CLOSED + AFTER_OPEN).format(c=char)
    elif kind.startswith("mark"):
        phrase, key = forced or rng.choice(MARKS)
        left = rng.choice(LEFT).format(p=page, c=char)
        mid = rng.choice(MID_MARK).format(c=char, o=phrase)
        after = (rng.choice(AFTER_CLOSED) if kind == "mark_closed"
                 else rng.choice(AFTER_OPEN)).format(c=char)
    else:
        phrase, key = forced or rng.choice(PROPS)
        left = rng.choice(LEFT).format(p=page, c=char)
        mid = rng.choice(MID_PROP).format(c=char, o=phrase)
        after = (rng.choice(AFTER_CLOSED) if kind == "closed_prop"
                 else rng.choice(AFTER_OPEN)).format(c=char)

    s = [Sentence(left, page, idx0), Sentence(mid, page, idx0 + 1),
         Sentence(after, page, idx0 + 2)]
    triad = Triad(*s)
    return triad, OBJ_BY_KEY[key], char, LABEL[kind]


def build_dataset(n_stories: int, seed: int, triads_per_story: int = 7):
    rng = random.Random(seed)
    examples = []
    for _ in range(n_stories):
        clue_kind = rng.choice(["closed_prop", "open_prop", "mark_closed",
                                "mark_open", "anomaly"])
        kinds = [clue_kind] + ["closed_prop"] * (triads_per_story - 1)
        rng.shuffle(kinds)
        for i, kind in enumerate(kinds):
            triad, concept, char, label = make_triad(rng, 10 + i // 3, kind,
                                                     idx0=i * 3)
            examples.append((features_for(triad, concept), label))
    return examples


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------

def evaluate(weights: dict[str, float], data) -> dict:
    tp = fp = fn = tn = 0
    loss = 0.0
    for fs, y in data:
        p = sigmoid(sum(weights.get(k, 0.0) for k in fs))
        p = min(max(p, 1e-9), 1 - 1e-9)
        pred = p >= 0.5
        if y and pred:
            tp += 1
        elif y and not pred:
            fn += 1
        elif not y and pred:
            fp += 1
        else:
            tn += 1
        loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"acc": (tp + tn) / len(data),
            "balanced": (recall + specificity) / 2,
            "f1": f1, "recall": recall, "precision": precision,
            "loss": loss / len(data), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def evaluate_scorer(scorer, data) -> dict:
    """То же, но для любого объекта с .predict (нужно для «младенца»)."""
    tp = fp = fn = tn = 0
    loss = 0.0
    for fs, y in data:
        p = min(max(scorer.predict(fs), 1e-9), 1 - 1e-9)
        pred = p >= 0.5
        tp += int(y and pred); fn += int(y and not pred)
        fp += int(not y and pred); tn += int(not y and not pred)
        loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"acc": (tp + tn) / len(data), "balanced": (recall + specificity) / 2,
            "f1": f1, "recall": recall, "precision": precision,
            "loss": loss / len(data), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def train(examples, val, epochs: int = 14, lr: float = 0.004,
          l2: float = 1e-4, seed: int = 1):
    rng = random.Random(seed)
    w: dict[str, float] = defaultdict(float)
    order = list(range(len(examples)))
    history = []
    t0 = time.time()
    for ep in range(epochs):
        rng.shuffle(order)
        rate = lr / (1 + 0.035 * ep)
        total_loss = 0.0
        for i in order:
            fs, y = examples[i]
            p = sigmoid(sum(w.get(k, 0.0) for k in fs))
            p = min(max(p, 1e-9), 1 - 1e-9)
            total_loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
            err = p - y
            for k in fs:
                w[k] = w.get(k, 0.0) * (1 - rate * l2) - rate * err
        v = evaluate(w, val)
        history.append({"epoch": ep + 1,
                        "loss": round(total_loss / len(examples), 4),
                        "val_balanced": round(v["balanced"], 4),
                        "val_f1": round(v["f1"], 4),
                        "val_acc": round(v["acc"], 4)})
        if ep == 0 or ep == epochs - 1 or (ep + 1) % 5 == 0:
            print(f"  эпоха {ep + 1:>2}/{epochs} | штраф {total_loss / len(examples):.4f} "
                  f"| F1 по уликам {v['f1'] * 100:5.1f}% "
                  f"| сбалансированная точность {v['balanced'] * 100:5.1f}%")
    return TrainedScorer(dict(w)), history, time.time() - t0


# ---------------------------------------------------------------------------
# Демо: история про Дворецкого
# ---------------------------------------------------------------------------

BUTLER_TRIADS = [
    (12, "На террасу вышел Дворецкий, держа в руках белый платок.",
     "Он вытер платком пот со лба и убрал его в карман.",
     "После этого Дворецкий спокойно начал стричь кусты."),
    (12, "Повар поставил на поднос фарфоровую чашку.",
     "Чашка звякнула, но Повар удержал ее.",
     "Он равнодушно продолжил мешать суп."),
    (12, "Из-под манжеты Дворецкого выглянул шрам-полумесяц.",
     "Дворецкий испуганно прикрыл шрам рукавом.",
     "Заметив взгляд Садовника, он убежал, ничего не объяснив."),
    (13, "Садовник достал мокрую газету.",
     "Он вытер газетой лейку.",
     "Садовник убрал газету в ведро и принялся за розы."),
    (13, "Бедняк Портной в латаном пальто вошел в гостиную.",
     "В его кармане тикали золотые часы.",
     "На вопрос Экономки Портной замялся и покраснел."),
    (13, "Горничная подняла с пола ложку.",
     "Она вымыла ложку и поставила на полку.",
     "Горничная спокойно вернулась к уборке."),
    (14, "Кучер шагнул навстречу, и все заметили его хромоту.",
     "Кучер оперся на трость, открыто показывая хромоту.",
     "Он равнодушно продолжил запрягать лошадь."),
]

CLUE_TRIAD = (480,
              "Прошло почти пятьсот страниц. Началась сорок восьмая глава.",
              "На месте убийства нашли белый платок и след шрама-полумесяца.",
              "Инспектор спросил: чья это вещь и кто прятал шрам?")


def butler_sentences():
    out, idx = [], 1
    for page, a, b, c in BUTLER_TRIADS:
        for t in (a, b, c):
            out.append(Sentence(t, page, idx)); idx += 1
    page, a, b, c = CLUE_TRIAD
    for t in (a, b, c):
        out.append(Sentence(t, page, idx, kind="clue")); idx += 1
    return out


# ---------------------------------------------------------------------------
# Этап 4: проверка боем на новых книгах
# ---------------------------------------------------------------------------

def build_book(rng: random.Random, seed_offset: int = 0):
    culprit = rng.choice(CHAR_NAMES)
    others = [c for c in CHAR_NAMES if c != culprit]
    clue_type = rng.choice(["mark", "open_prop", "beacon_prop"])

    if clue_type == "mark":
        kind, pool = rng.choice(["mark_closed", "mark_open"]), MARKS
    elif clue_type == "open_prop":
        kind, pool = "open_prop", PROPS
    else:
        kind, pool = "closed_prop", PROPS

    forced = rng.choice(pool)
    clue_key = forced[1]
    used_keys = {clue_key}
    triads = [make_triad(rng, 12, kind, char=culprit, forced=forced, idx0=0)]
    for i in range(8):
        pool2 = [p for p in PROPS if p[1] not in used_keys]
        pick = rng.choice(pool2)
        used_keys.add(pick[1])
        triads.append(make_triad(rng, 13 + i // 3, "closed_prop",
                                 char=rng.choice(others), forced=pick,
                                 idx0=(i + 1) * 3))
    rng.shuffle(triads)

    sentences = []
    for t, _, _, _ in triads:
        sentences.extend([t.before, t.now, t.after])
    base = len(sentences) + 1
    sentences.append(Sentence("Прошло пятьсот страниц.", 500, base, kind="clue"))
    sentences.append(Sentence(f"На месте убийства нашли {forced[0]}.", 500,
                              base + 1, kind="clue"))
    sentences.append(Sentence("Кто это был?", 500, base + 2, kind="clue"))
    return sentences, culprit, clue_key, clue_type, triads


def full_text_answer(triads, key: str) -> str | None:
    """Обычный ИИ с полным контекстом: ищет улику прямо в исходном тексте."""
    target = feat(key)
    for t, _, _, _ in triads:
        if any(feat(w) == target
               for sent in (t.before, t.now, t.after) for w in tokens(sent.text)):
            cs = find_chars(t.before.text) or find_chars(t.now.text)
            if cs:
                return cs[0]
    return None


def run_heldout(n_books: int, scorer, mode: str, seed: int = 9001):
    rng = random.Random(seed)
    hits = 0
    by_type = defaultdict(lambda: [0, 0])
    retained = original = 0
    tp = fp = fn = tn = 0
    for b in range(n_books):
        sentences, culprit, key, ctype, triads = build_book(rng, b)
        original += sum(len(s.text.encode("utf-8")) for s in sentences)
        ans = None
        if mode == "keep_all":
            ans = full_text_answer(triads, key)
            retained += sum(len(s.text.encode("utf-8")) for s in sentences)
        elif mode != "trash_all":
            net = GestaltNet(scorer, verbose=False)
            net.read_many(sentences)
            if mode == "full":
                ans = net.answer_by_mark(key) or net.answer_by_prop(key)
                retained += net.retained_bytes()
            elif mode == "no_beacons":
                ans = net.answer_by_mark(key)
            elif mode == "no_dossier":
                ans = net.answer_by_prop(key)
            # качество решений «оставить / стереть» считается у самой сети
            for d in net.decisions:
                pred, truth = d.box == "dossier", d.correct
                tp += int(pred and truth); fp += int(pred and not truth)
                fn += int(not pred and truth); tn += int(not pred and not truth)
        ok = int(ans == culprit)
        hits += ok
        by_type[ctype][0] += ok
        by_type[ctype][1] += 1
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    return {"accuracy": round(hits / n_books, 4), "hits": hits, "n": n_books,
            "by_type": {k: {"hits": v[0], "n": v[1], "accuracy": round(v[0] / v[1], 3)}
                        for k, v in by_type.items()},
            "dossier_precision": round(prec, 4), "decision_f1": round(f1, 4),
            "retained_bytes": retained, "original_bytes": original}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    R: dict = {"meta": {"python": sys.version.split()[0],
                        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "device": "CPU, чистый Python, без внешних библиотек"}}

    print("=" * 78)
    print("ЭТАП 2. Готовим 1000 учебных рассказов-загадок")
    print("=" * 78)
    train_data = build_dataset(1000, seed=11)
    val_data = build_dataset(250, seed=777)
    pos_share = sum(y for _, y in train_data) / len(train_data)
    R["dataset"] = {"train_stories": 1000, "val_stories": 250,
                    "train_examples": len(train_data), "val_examples": len(val_data),
                    "positive_share": round(pos_share, 3),
                    "always_trash_acc": round(1 - pos_share, 3)}
    print(f"  рассказов: 1000 обучающих + 250 проверочных; решений: "
          f"{len(train_data)} + {len(val_data)}")
    print(f"  настоящих улик всего {pos_share * 100:.1f}% → если ВСЁ выбрасывать, "
          f"формальная точность уже {(1 - pos_share) * 100:.1f}%.")
    print("  Поэтому главная метрика — F1 по классу «улика» и сбалансированная точность.")

    baby = RandomScorer(seed=7)
    baby_metrics = {k: round(v, 4) if isinstance(v, float) else v
                    for k, v in evaluate_scorer(baby, val_data).items()}

    print("\n" + "=" * 78)
    print("ЭТАП 3. Обучение: сеть ошибается, получает штраф, меняет веса")
    print("=" * 78)
    print(f"  ДО обучения: F1 по уликам {baby_metrics['f1'] * 100:.1f}%, "
          f"сбалансированная точность {baby_metrics['balanced'] * 100:.1f}%.\n")
    scorer, history, seconds = train(train_data, val_data, epochs=14)
    final = evaluate(scorer.weights, val_data)
    R["training"] = {
        "before": baby_metrics,
        "after": {k: round(v, 4) if isinstance(v, float) else v for k, v in final.items()},
        "seconds": round(seconds, 2), "history": history,
        "n_weights": len(scorer.weights), "epochs": len(history),
    }

    by_prefix = defaultdict(float)
    for k, v in scorer.weights.items():
        by_prefix[k.split(":")[0] if ":" in k else k] += abs(v)
    R["training"]["weight_mass"] = {k: round(v, 1) for k, v in
                                    sorted(by_prefix.items(), key=lambda x: -x[1])[:6]}
    R["training"]["after_sentence_mass"] = round(
        sum(abs(v) for k, v in scorer.weights.items() if k.startswith("A:")), 1)
    R["training"]["first_sentence_mass"] = round(
        sum(abs(v) for k, v in scorer.weights.items() if k.startswith("L:")), 1)
    R["training"]["middle_sentence_mass"] = round(
        sum(abs(v) for k, v in scorer.weights.items() if k.startswith("M:")), 1)

    pretty = {"испу": "испуганно", "убеж": "убежал", "дрог": "дрогнул",
              "покр": "покраснел", "замя": "замялся", "спок": "спокойно",
              "равн": "равнодушно", "прод": "продолжил", "убра": "убрал",
              "верн": "вернулся", "шрам": "шрам", "тату": "татуировка",
              "хром": "хромота", "плат": "платок", "чашк": "чашка",
              "золо": "золотые часы", "бедн": "бедняк", "нищи": "нищий",
              "ложк": "ложка", "газе": "газета", "объя": "объяснив",
              "торо": "торопливо", "скры": "скрылся", "темн": "в темноте",
              "поспе": "поспешно", "огля": "оглянулся", "заше": "зашептал",
              "латан": "латаном", "дыря": "дырявом", "обыч": "обычные дела",
              "отло": "отложил", "приня": "принялся", "вещь": "вещь",
              "дела": "дела", "голо": "голос", "глаз": "глаза",
              "седи": "седина", "роди": "родинка", "жемч": "жемчужный",
              "сере": "серебряное", "коль": "кольцо", "меда": "медальон",
              "BIAS": "сдвиг", "OBJ": "предмет"}
    ranked = sorted(scorer.weights.items(), key=lambda x: -x[1])
    pos, neg = [], []
    for k, v in ranked:
        stem = k.split(":")[-1]
        if v > 0.1 and len(pos) < 10:
            pos.append({"feature": k, "word": pretty.get(stem, stem), "weight": round(v, 3)})
    for k, v in reversed(ranked):
        stem = k.split(":")[-1]
        if v < -0.1 and len(neg) < 10:
            neg.append({"feature": k, "word": pretty.get(stem, stem), "weight": round(v, 3)})
    R["training"]["top_positive"] = pos
    R["training"]["top_negative"] = neg

    print(f"\n  ПОСЛЕ: F1 {final['f1'] * 100:.1f}%, сбалансированная точность "
          f"{final['balanced'] * 100:.1f}% за {seconds:.1f} сек на CPU.")
    print(f"  Проверка «ждала ли сеть 3-е предложение»: сумма |весов| слов из "
          f"ПОСЛЕ = {R['training']['after_sentence_mass']}, "
          f"из ДО = {R['training']['first_sentence_mass']}.")

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("ДЕМО. Читаем историю про Дворецкого (обученная сеть)")
    print("=" * 78)
    sent = butler_sentences()

    baby_net = GestaltNet(RandomScorer(seed=7), verbose=False)
    baby_net.read_many(sent)
    R["demo_untrained"] = {"mistakes": baby_net.mistakes(),
                           "total": len(baby_net.decisions),
                           "examples": [
                               {"obj": d.obj.key, "box": d.box,
                                "truth": "dossier" if d.correct else "trash",
                                "score": round(d.score, 2)}
                               for d in baby_net.decisions
                               if (d.box == "dossier") != d.correct][:5]}

    net = GestaltNet(scorer, verbose=True)
    net.read_many(sent)
    orig, kept = net.original_bytes(sent), net.retained_bytes()
    R["demo"] = {
        "trace": net.trace,
        "dossier": dict(net.dossier),
        "beacons": {k: [{"char": c, "page": p} for c, p in v]
                    for k, v in net.beacons.items()},
        "decisions": [{"obj": d.obj.key, "char": d.char, "score": round(d.score, 3),
                       "box": d.box, "permanent": d.permanent,
                       "unresolved": d.unresolved, "closed": d.closed,
                       "anomaly": d.anomaly, "page": d.triad.now.page,
                       "beacon": d.beacon_written, "tag": d.tag}
                      for d in net.decisions],
        "triads": net.triads_seen, "mistakes": net.mistakes(),
        "sentences": len(sent),
        "original_bytes": orig, "retained_bytes": kept,
        "saved_pct": round((1 - kept / orig) * 100, 1),
        "answer_mark": net.answer_by_mark("шрам"),
        "answer_prop": net.answer_by_prop("платок"),
        "beacon_hits": [t["text"] for t in net.trace if t["kind"] == "beacon_hit"],
    }
    print("\n" + "-" * 78)
    print(f"ИТОГ ДЕМО: предложений {len(sent)}, троек {net.triads_seen}, ошибок {net.mistakes()}")
    print(f"  память: {orig} байт текста -> {kept} байт (досье + маячки), "
          f"экономия {R['demo']['saved_pct']}%")
    print(f"  «кто прятал шрам?» -> {R['demo']['answer_mark']}; "
          f"«чей платок?» -> {R['demo']['answer_prop']}")

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("ЭТАП 4. Проверка боем: 200 книг, которых сеть никогда не видела")
    print("=" * 78)
    labels = {"keep_all": "Обычный ИИ: хранит ВСЁ (верхняя граница)",
              "full": "Гештальт-Сеть: 3 коробки целиком",
              "no_beacons": "Абляция: убрали коробку 3 (маячки)",
              "no_dossier": "Абляция: убрали коробку 1 (досье)",
              "trash_all": "Контроль: стирает всё подряд",
              "untrained": "Та же архитектура, но НЕОБУЧЕННАЯ",
              "untrained_no_beacons": "Необученная + без маячков"}
    R["heldout"] = {}
    for m in ["keep_all", "full", "no_beacons", "no_dossier", "trash_all"]:
        R["heldout"][m] = {**run_heldout(200, scorer, m), "label": labels[m]}
    R["heldout"]["untrained"] = {
        **run_heldout(200, RandomScorer(seed=7), "full", seed=9001),
        "label": labels["untrained"]}
    R["heldout"]["untrained_no_beacons"] = {
        **run_heldout(200, RandomScorer(seed=7), "no_beacons", seed=9001),
        "label": labels["untrained_no_beacons"]}
    for m, res in R["heldout"].items():
        extra = ""
        if m in ("full", "untrained"):
            extra = f" | точность досье {res['dossier_precision'] * 100:.0f}% " \
                    f"| память {res['retained_bytes']:,} байт"
        print(f"  {labels[m]:<44} {res['accuracy'] * 100:5.1f}%{extra}")

    full, keep = R["heldout"]["full"], R["heldout"]["keep_all"]
    untr = R["heldout"]["untrained"]
    R["memory"] = {
        "books": 200,
        "keep_all_bytes": keep["retained_bytes"],
        "gestalt_bytes": full["retained_bytes"],
        "untrained_bytes": untr["retained_bytes"],
        "saved_pct": round((1 - full["retained_bytes"] / keep["retained_bytes"]) * 100, 1),
        "ratio": round(keep["retained_bytes"] / full["retained_bytes"], 1),
        "extra_cost_untrained_pct": round(
            (untr["retained_bytes"] / full["retained_bytes"] - 1) * 100, 1),
        "per_book_keep": round(keep["retained_bytes"] / 200),
        "per_book_gestalt": round(full["retained_bytes"] / 200),
    }
    novel_bytes = 500 * 1800 * 2  # 500 страниц × 1800 символов × ~2 байта (кириллица)
    R["memory"]["novel_extrapolation"] = {
        "novel_bytes": novel_bytes,
        "gestalt_bytes": round(novel_bytes * full["retained_bytes"] / keep["retained_bytes"]),
        "saved_bytes": round(novel_bytes * (1 - full["retained_bytes"] / keep["retained_bytes"])),
        "note": "Экстраполяция по измеренной доле, а не замер реального романа.",
    }
    m = R["memory"]
    print(f"\n  ПАМЯТЬ на 200 книгах: {keep['retained_bytes']:,} -> "
          f"{full['retained_bytes']:,} байт. Экономия {m['saved_pct']}% "
          f"(в {m['ratio']} раза меньше контекста).")
    print(f"  Необученная сеть хранит {untr['retained_bytes']:,} байт — "
          f"на {m['extra_cost_untrained_pct']}% больше.")

    OUT.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ Результаты записаны в {OUT}")
    return R


if __name__ == "__main__":
    main()
