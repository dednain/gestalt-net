# -*- coding: utf-8 -*-
"""
Гештальт-Сеть: ядро архитектуры (правило трёх предложений + три коробки памяти).

Никаких внешних зависимостей — только стандартная библиотека Python.

Идея:
  1) Сеть читает текст ТРОЙКАМИ:  ДО -> СЕЙЧАС -> ПОСЛЕ.
     Судьба предмета из среднего предложения НЕ решается, пока не прочитано
     третье предложение (развязка действия).
  2) Решение принимает обучаемый оценщик (linear scorer), а не жёсткий if/else.
     Оценщик выдаёт вероятность "это нужно оставить в вечном досье".
  3) Три коробки:
       📦 dossier  — вечные приметы и подозрительные вещи (короткий ярлык);
       🗑️ trash    — закрытые бытовые действия (стираются НАСОВСЕМ, пустая коробка);
       📡 beacons  — крошечные "пароли" вида  платок = Дворецкий (стр. 12),
                     которые записываются ПЕРЕД тем, как вещь улетает в мусорку.
"""

from __future__ import annotations

import math
import random
import re
import zlib
from dataclasses import dataclass, field
from typing import Iterable

TOKEN_RE = re.compile(r"[а-яa-z0-9ё-]+", re.I)


def norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(norm(text))


def feat(word: str) -> str:
    """Грубый, но очень стабильный стем: первые 4 буквы.

    Для русской морфологии этого достаточно, чтобы 'платок', 'платка',
    'платком' и 'убрал/убрала/убрали' попадали в одну и ту же ячейку признаков.
    """
    w = norm(word)
    return w[:4] if len(w) > 4 else w


# ---------------------------------------------------------------------------
# Словари понятий. Это «глаза» сети: она пока не знает грамматику, но умеет
# узнавать предметы и персонажей по устойчивым началам слов.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    kind: str                    # "body" (часть тела) или "prop" (вещь в руках)
    stems: tuple[str, ...]
    luxury: bool = False


OBJECTS: tuple[Concept, ...] = (
    Concept("шрам", "шрам-полумесяц", "body", ("шрам",)),
    Concept("татуировка", "татуировка", "body", ("тату",)),
    Concept("хромота", "хромота", "body", ("хром",)),
    Concept("родинка", "родинка", "body", ("роди",)),
    Concept("седина", "седина", "body", ("седи",)),
    Concept("платок", "белый платок", "prop", ("плат",)),
    Concept("чашка", "чашка", "prop", ("чашк", "чаше")),
    Concept("ложка", "ложка", "prop", ("ложк", "ложе")),
    Concept("газета", "газета", "prop", ("газет",)),
    Concept("зонт", "зонт", "prop", ("зонт",)),
    Concept("перчатка", "перчатка", "prop", ("перча",)),
    Concept("ключ", "ключ", "prop", ("ключ",)),
    Concept("письмо", "письмо", "prop", ("письм",)),
    Concept("стакан", "стакан", "prop", ("стака",)),
    Concept("тарелка", "тарелка", "prop", ("тарел",)),
    Concept("пуговица", "пуговица", "prop", ("пугов",)),
    Concept("кошелек", "кошелек", "prop", ("коше",)),
    Concept("фонарь", "фонарь", "prop", ("фонар",)),
    Concept("щетка", "щетка", "prop", ("щетк",)),
    Concept("золотые часы", "золотые часы", "prop", ("золот", "часы", "часо"), True),
    Concept("медальон", "жемчужный медальон", "prop", ("жемч", "меда"), True),
    Concept("кольцо", "серебряное кольцо", "prop", ("сере", "коль"), True),
)

CHARS: tuple[tuple[str, str], ...] = (
    ("Дворецкий", "дворецк"),
    ("Садовник", "садовн"),
    ("Повар", "повар"),
    ("Горничная", "горни"),
    ("Кучер", "кучер"),
    ("Библиотекарь", "библи"),
    ("Лакей", "лаке"),
    ("Экономка", "эконом"),
    ("Портной", "портн"),
    ("Аптекарь", "аптек"),
)

# Маркеры НЕЗАКРЫТОЙ эмоции / тайны (обычно в 3-м предложении).
UNRESOLVED = ("испуг", "поспе", "дрож", "пряч", "спрят", "огля", "шепо", "шепч",
              "покра", "замял", "стран", "трев", "замер", "вздро", "убежа",
              "тороп", "скрыл", "прикры", "темно", "глаза", "объяс")

# Маркеры ЗАКРЫТОГО бытового действия (переключился на другое дело).
CLOSED = ("убрал", "убрат", "поста", "доста", "вытер", "закры", "начал",
          "приня", "верну", "споко", "равно", "забыл", "отлож", "продо",
          "вымыл", "полож", "обычн", "делам", "работ")

# Контраст «роскошь у бедняка» -> подозрение.
POVERTY = ("бедн", "нищи", "латан", "дыря", "стар", "обно")


def has_cue(text: str, cues: Iterable[str]) -> bool:
    fs = {feat(t) for t in tokens(text)}
    words = list(tokens(text))
    for c in cues:
        if any(w.startswith(c) or feat(w).startswith(feat(c)[:4]) for w in words):
            return True
        if feat(c) in fs:
            return True
    return False


def find_objects(text: str) -> list[Concept]:
    fs = {feat(t) for t in tokens(text)}
    out = []
    for c in OBJECTS:
        if any(feat(s) in fs for s in c.stems):
            out.append(c)
    return out


def find_chars(text: str) -> list[str]:
    fs = {feat(t) for t in tokens(text)}
    return [name for name, stem in CHARS if feat(stem) in fs]


# ---------------------------------------------------------------------------
# Данные
# ---------------------------------------------------------------------------

@dataclass
class Sentence:
    text: str
    page: int
    idx: int
    # "action" — обычное повествование; "clue" — улика/вопрос в конце книги:
    # такие предложения только БУДЯТ маячки, но не создают новых решений.
    kind: str = "action"


@dataclass
class Triad:
    before: Sentence
    now: Sentence
    after: Sentence

    @property
    def all_text(self) -> str:
        return " ".join(s.text for s in (self.before, self.now, self.after))


@dataclass
class Decision:
    triad: Triad
    char: str
    obj: Concept
    score: float
    box: str                       # "dossier" | "trash"
    permanent: bool
    unresolved: bool
    closed: bool
    anomaly: bool
    beacon_written: str = ""
    tag: str = ""

    @property
    def correct(self) -> bool:
        """Истина, известная только «учителю» (для подсчёта ошибок)."""
        return (self.permanent or self.unresolved or self.anomaly)


def features_for(triad: Triad, obj: Concept) -> set[str]:
    """Признаки, которые видит оценщик.

    Важно: сюда входят слова ИЗ ТРЕТЬЕГО предложения с префиксом A:.
    Если после обучения веса A:... окажутся большими — значит, сеть
    действительно научилась «ждать развязки».
    """
    f: set[str] = {"BIAS", "OBJ:" + feat(obj.key)}
    for prefix, sent in (("L:", triad.before), ("M:", triad.now), ("A:", triad.after)):
        for t in tokens(sent.text):
            f.add(prefix + feat(t))
    # ВАЖНО: сети НЕ подсказывают, «часть тела» это или «вещь».
    # Она должна сама выучить это по словам — иначе обучение было бы фальшивым.
    return f


# ---------------------------------------------------------------------------
# Оценщики
# ---------------------------------------------------------------------------

class RandomScorer:
    """«Младенец»: случайные веса, сеть ещё ничего не умеет."""

    def __init__(self, seed: int = 7):
        self.seed = seed
        self.weights: dict[str, float] = {}
        self.cache: dict[str, float] = {}

    def _w(self, k: str) -> float:
        # Вес считается из хеша имени признака, а не из последовательного RNG:
        # иначе порядок обхода set (а значит и результат) менялся бы от
        # запуска к запуску из-за рандомизации хешей в Python.
        if k not in self.cache:
            h = zlib.crc32(f"{self.seed}:{k}".encode("utf-8"))
            self.cache[k] = ((h % 10000) / 5000.0 - 1.0) * 0.35
        return self.cache[k]

    def score(self, fs: set[str]) -> float:
        return sum(self._w(k) for k in fs)

    def predict(self, fs: set[str]) -> float:
        return 1.0 / (1.0 + math.exp(-max(-30, min(30, self.score(fs)))))


class TrainedScorer:
    """Обученный логистический оценщик (те же «нейроны», но с весами)."""

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights: dict[str, float] = dict(weights or {})

    def score(self, fs: set[str]) -> float:
        w = self.weights
        return sum(w.get(k, 0.0) for k in fs)

    def predict(self, fs: set[str]) -> float:
        return 1.0 / (1.0 + math.exp(-max(-30, min(30, self.score(fs)))))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-min(x, 30)))
    e = math.exp(max(x, -30))
    return e / (1.0 + e)


# ---------------------------------------------------------------------------
# Сама сеть
# ---------------------------------------------------------------------------

class GestaltNet:
    def __init__(self, scorer, threshold: float = 0.5, verbose: bool = True):
        self.scorer = scorer
        self.threshold = threshold
        self.verbose = verbose
        self.buffer: list[Sentence] = []
        self.dossier: dict[str, list[str]] = {}
        self.dossier_by_key: dict[str, str] = {}   # точный индекс: ключ предмета -> владелец
        self.beacons: dict[str, list[tuple[str, int]]] = {}
        self.decisions: list[Decision] = []
        self.trash_count = 0
        self.trash_bytes = 0
        self.trace: list[dict] = []
        self.last_char = "?"
        self.triads_seen = 0

    # --- вывод -------------------------------------------------------------
    def log(self, kind: str, text: str, **extra) -> None:
        self.trace.append({"kind": kind, "text": text, **extra})
        if self.verbose:
            print(text)

    # --- чтение ------------------------------------------------------------
    def read(self, s: Sentence) -> None:
        """Прочитать одно предложение. Маячки проверяются СРАЗУ,
        а решения по предметам — только когда собралась вся тройка."""
        self.log("read", f"  стр. {s.page:>3} | читаю: {s.text}")

        # 📡 Маячок срабатывает на любом входящем предложении.
        for obj in find_objects(s.text):
            hits = self.beacons.get(obj.key, [])
            for char, page in hits:
                self.log("beacon_hit",
                         f"  🔔 МАЯЧОК ПИЩИТ: «{obj.key}» = {char} (стр. {page}). "
                         f"Сеть мгновенно вспомнила владельца!",
                         obj=obj.key, char=char, page=page)

        found = find_chars(s.text)
        if found:
            self.last_char = found[0]

        self.buffer.append(s)
        if len(self.buffer) == 3:
            triad = Triad(*self.buffer)
            self.triads_seen += 1
            self._resolve(triad)
            self.buffer = []

    def read_many(self, sentences: Iterable[Sentence]) -> None:
        for s in sentences:
            self.read(s)
        self.flush()

    def flush(self) -> None:
        """Хвост текста: если тройка не собралась, развязка считается пустой."""
        if not self.buffer:
            return
        while len(self.buffer) < 3:
            last = self.buffer[-1]
            self.buffer.append(Sentence("Дальше ничего не произошло.", last.page,
                                       last.idx + 1, kind=last.kind))
        self._resolve(Triad(*self.buffer))
        self.buffer = []

    # --- главное правило ---------------------------------------------------
    def _resolve(self, triad: Triad) -> None:
        n = self.triads_seen
        if all(s.kind == "clue" for s in (triad.before, triad.now, triad.after)):
            self.log("clue", f"\n[УЛИКА] Развязки нет — только сверка с маячками и досье.")
            return
        self.log("triad", f"\n[ТРОЙКА {n}] ДО: {triad.before.text}")
        self.log("triad", f"           СЕЙЧАС: {triad.now.text}")
        self.log("triad", f"           ПОСЛЕ: {triad.after.text}")
        self.log("wait", f"  ⏳ Решение отложено до 3-го предложения: «{triad.after.text}»")

        chars = find_chars(triad.now.text) or find_chars(triad.before.text) or [self.last_char]
        char = chars[0]
        objs = find_objects(triad.now.text)

        if not objs:
            self.log("skip", "  · в среднем предложении нет предметов — нечего решать")
            return

        for obj in objs:
            fs = features_for(triad, obj)
            p = self.scorer.predict(fs)
            permanent = obj.kind == "body"
            unresolved = has_cue(triad.after.text, UNRESOLVED) or has_cue(triad.now.text, UNRESOLVED)
            closed = has_cue(triad.after.text, CLOSED)
            anomaly = obj.luxury and (has_cue(triad.all_text, POVERTY) or has_cue(triad.now.text, UNRESOLVED))

            d = Decision(triad, char, obj, p, "dossier" if p >= self.threshold else "trash",
                         permanent, unresolved, closed, anomaly)
            self.decisions.append(d)

            checks = (f"навсегда={'ДА' if permanent else 'нет'} | "
                      f"тайна={'ДА' if unresolved else 'нет'} | "
                      f"действие закрыто={'ДА' if closed else 'нет'} | "
                      f"контраст={'ДА' if anomaly else 'нет'}")
            self.log("check", f"  ❓ «{obj.key}» у {char}: {checks}")
            self.log("score", f"  🧮 оценка сети = {p:.2f} (порог {self.threshold:.2f})")

            if d.box == "dossier":
                tag = f"{obj.label} (стр. {triad.now.page})"
                if tag not in self.dossier.setdefault(char, []):
                    self.dossier[char].append(tag)
                self.dossier_by_key.setdefault(obj.key, char)
                d.tag = tag
                self.log("keep", f"  📦 ВЕЧНОЕ ДОСЬЕ: к карточке [{char}] приклеен ярлык «{tag}». "
                                 f"Длинные предложения удалены.")
            else:
                # Сначала пароль-маячок, ПОТОМ мусорка.
                page = triad.now.page
                bucket = self.beacons.setdefault(obj.key, [])
                if (char, page) not in bucket:
                    bucket.append((char, page))
                d.beacon_written = f"{obj.key} = {char} (стр. {page})"
                self.trash_count += 1
                self.trash_bytes += sum(len(x.text.encode("utf-8")) for x in
                                       (triad.before, triad.now, triad.after))
                self.log("beacon", f"  📡 СПЯЩИЙ МАЯЧОК записан: «{d.beacon_written}». Весит 22 байта.")
                self.log("erase", f"  🗑️ МУСОРКА: сцена с «{obj.key}» полностью стёрта из оперативной памяти.")

    # ---_MEMORY / отчёт ----------------------------------------------------
    def retained_bytes(self) -> int:
        """Сколько байт реально осталось в памяти (досье + маячки)."""
        n = 0
        for char, tags in self.dossier.items():
            n += len(char.encode("utf-8"))
            for t in tags:
                n += len(t.encode("utf-8")) + 4
        for key, hits in self.beacons.items():
            n += len(key.encode("utf-8"))
            for char, page in hits:
                n += len(char.encode("utf-8")) + len(str(page)) + 6
        return n

    def original_bytes(self, sentences: Iterable[Sentence]) -> int:
        return sum(len(s.text.encode("utf-8")) for s in sentences)

    def answer_by_mark(self, mark_key: str) -> str | None:
        if mark_key in self.dossier_by_key:
            return self.dossier_by_key[mark_key]
        for char, tags in self.dossier.items():
            if any(feat(mark_key) in feat(t) for t in tags):
                return char
        return None

    def answer_by_prop(self, prop_key: str) -> str | None:
        hits = self.beacons.get(prop_key, [])
        return hits[0][0] if hits else None

    def mistakes(self) -> int:
        """Сколько решений разошлись с истиной (для «младенца»)."""
        return sum(1 for d in self.decisions if (d.box == "dossier") != d.correct)
