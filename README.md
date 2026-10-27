# Gestalt-Net

**Long-term memory for text that runs on a toaster.**

`Python 3.8+` · **zero dependencies** · **no GPU** · **no PyTorch** · **no vector database** · **409-line core**, 1,225 lines of Python total

[![verify](https://img.shields.io/badge/verify-27%2F27-brightgreen)]() [![deps](https://img.shields.io/badge/dependencies-0-blue)]() [![license](https://img.shields.io/badge/license-MIT-green)]()

---

## The problem

Long-context systems are hoarders. A transformer keeps every token in its KV cache; a RAG pipeline keeps every chunk in an index; a "memory" wrapper keeps every turn in a prompt buffer. All of them grow with the length of the text.

Gestalt-Net starts from a different observation: **most sentences are domestic noise**. A butler takes out a handkerchief, wipes his forehead, puts it away — and nothing ever comes of it. But a scar is part of a body, and a frightened reaction is an unfinished story. A reader should be allowed to forget the first and remember the second, while leaving itself a tiny note about what it threw away.

This repository is a deliberately small, fully reproducible implementation of that idea. It is **not** a language model. It is a memory controller — the bookkeeping layer you would put *around* a model.

---

## 1. The sliding window: why we always wait for the third sentence

Let the text be a stream of sentences

$$S = (s_1, s_2, \dots, s_T).$$

The reader consumes **non-overlapping triads**

$$\tau_k = (b_k,\ c_k,\ a_k) = (s_{3k-2},\ s_{3k-1},\ s_{3k}) \quad\text{— BEFORE, CURRENT, AFTER.}$$

For an object $o$ mentioned in the CURRENT sentence, the decision is **undefined until the AFTER sentence has been read**:

$$d(o \mid b, c) = \bot, \qquad d(o \mid b, c, a) = \mathbb{1}\big[\,p(o,\tau) \ge \theta\,\big].$$

This is the formal version of "never judge the handkerchief until you know how the scene ended". In code it is a three-sentence buffer: `read()` appends a sentence, and `_resolve()` fires only when the buffer reaches length 3. Consequences:

- *"Убрал платок в карман и начал стричь кусты"* → closed action → the scene is erasable.
- *"Испуганно спрятал рукав со шрамом и убежал"* → unresolved emotion → the scene is evidence.

### What the scorer actually sees

Features are stem-like 4-character prefixes, prefixed by their position in the triad (the stem is deliberately crude but deterministic and dependency-free):

$$x(o,\tau) = \{\texttt{L:}\mathrm{stem}(w): w \in b\} \cup \{\texttt{M:}\mathrm{stem}(w): w \in c\} \cup \{\texttt{A:}\mathrm{stem}(w): w \in a\} \cup \{\texttt{OBJ:}\mathrm{stem}(o), \texttt{BIAS}\}.$$

$$p(o,\tau) = \sigma\!\left(\sum_{f \in x(o,\tau)} w_f\right), \qquad \sigma(z) = \frac{1}{1+e^{-z}}, \qquad \theta = 0.5.$$

Training is plain logistic regression with SGD on the binary "does this belong in the Dossier" label:

$$\mathcal{L} = -\frac{1}{N}\sum_i \Big[y_i \log p_i + (1-y_i)\log(1-p_i)\Big] + \lambda \lVert w \rVert_2^2, \qquad w_f \leftarrow w_f - \eta\,(p_i - y_i) - \eta\lambda w_f.$$

**1,000 templated stories → 7,000 decisions, 198 learned weights, 14 epochs, about one second on a single CPU core.** F1 on the "evidence" class goes from **19.9% → 100%**.

> **Did it really learn to wait?** Yes — measurably. After training, the summed absolute weight mass of the AFTER features is $\sum_{f\in A}|w_f| = 25.8$, against $9.5$ for BEFORE features. That is **2.7× more influence from the third sentence than from the first**. (CURRENT: 34.7 — dominated by the object name itself — and `OBJ:`: 8.0.) The network was never told which sentence matters; it discovered the resolution.

---

## 2. Three boxes of memory

| Box | Contents | Size model |
|---|---|---|
| 📦 **Dossier** (eternal) | Permanent traits (scar, tattoo, limp) and anything that produced a strange reaction or a suspicious contrast — e.g. gold watches on a poor man. Long sentences are deleted; only a short tag survives: `[Butler] → crescent scar (p. 12)`. | $O(\text{entities})$ tags |
| 🗑️ **Trash** | Closed domestic actions. The scene is erased **completely** from working memory. Empty by definition — it costs nothing. | $0$ |
| 📡 **Sleep-Triggers** | Written **before** deletion: `handkerchief = Butler (p. 12)`. Five hundred pages later, if a white handkerchief appears, an $O(1)$ dictionary lookup fires and the owner is recalled. | $O(\text{forgotten objects})$, ~22 bytes each |

Formally, for a text of $T$ sentences:

$$B_{\text{full}} = \sum_{i=1}^{T} \text{bytes}(s_i), \qquad B_{\text{ges}} = \sum_{\text{tags}} \text{bytes}(\text{tag}) + \sum_{\text{triggers}} \text{bytes}(\text{key}, \text{owner}, \text{page}).$$

Measured on 200 unseen books:

$$B_{\text{full}} = 478{,}193 \text{ bytes} \;\longrightarrow\; B_{\text{ges}} = 64{,}716 \text{ bytes}, \qquad 1 - \frac{B_{\text{ges}}}{B_{\text{full}}} = \mathbf{86.5\%}, \qquad \frac{B_{\text{full}}}{B_{\text{ges}}} = \mathbf{7.4\times}.$$

That is the whole trick: **replace retained prose with tags and triggers**. No quantization, no pruning, no attention kernel — just retrospective logic.

---

## 3. Benchmark (all numbers from `results.json`, regenerable in ~6 s)

| Metric | Result |
|---|---|
| Training set | 1,000 stories / 7,000 decisions; 250 validation stories / 1,750 decisions |
| Class imbalance | only 11.2% are real evidence → "delete everything" already scores 88.8% accuracy, so we report **F1 on the evidence class** |
| F1 before → after training | **19.9% → 100.0%** (198 TP, 0 FP, 0 FN, 1,552 TN) |
| Training time | **~1 s** (0.6–1.2 s depending on CPU), pure Python, CPU only, 14 epochs |
| Weight mass AFTER / BEFORE | **25.8 / 9.5 = 2.7×** |
| Butler demo | 24 sentences, 8 triads, 7 decisions, **0 mistakes**; 1,956 → 295 bytes (**−84.9%**) |
| 200 unseen books, answer accuracy | **100%** (full) · 100% (keep-everything oracle) · 60% (no triggers) · 40% (no Dossier) · 0% (delete everything) |
| Dossier precision | **100%** trained vs **7.5%** untrained |
| Memory, 200 books | 478,193 → 64,716 bytes = **−86.5%**, **7.4×** context compression |
| Independent verification | **27/27 checks pass**, exit code 0 |

### Ablations are the interesting part

Remove the Sleep-Triggers and accuracy drops to 60%; remove the Dossier and it drops to 40%. **All three boxes are load-bearing.** But note the honest twist: an *untrained* network still answers 100% correctly, because the triggers insure against almost any bad decision. Training does not buy correctness here — it buys a clean Dossier and small memory (untrained retained 68,752 bytes, ~6% worse). That is a direct consequence of box 3, not a bug.

---

## 4. Honest scope — please read before citing

1. **This is not a transformer and not an LLM.** The scorer is a linear bag-of-stems logistic model over three sentences. In a production system an LLM would play the judge; the three boxes are the external memory around it.
2. **The corpus is synthetic** — templated Russian detective stories. Real prose needs proper NER, coreference resolution and irony handling. The 100% accuracy is on *these* books, not on literature.
3. **We measured UTF-8 bytes of retained text, not VRAM.** These are *not* KV-cache bytes and not GPU memory. Translating −86.5% retained context into VRAM savings requires a real transformer integration, which we have not done. The 500-page extrapolation (1.8 MB → ~0.24 MB) is arithmetic from the measured ratio, not a benchmark.
4. **The label is a dense surrogate** ("should this object be in the Dossier"), derived from whether the final answer needed it. Full reinforcement learning from a single end-of-book reward is future work.
5. **Stems, characters and cue words are hand-listed lexicons.** The *decision* is learned; the *perception* is not.

---

## 5. Quickstart

No `pip install`. No virtualenv. No GPU. Just Python 3.

```bash
git clone https://github.com/dednain/gestalt-net.git
cd gestalt-net

python3 gestalt/verify.py        # 27/27 independent checks, ~3 s
python3 gestalt/experiment.py    # full benchmark, ~6 s, rewrites results.json
python3 gestalt/verify.py --log  # optional: write verification_log.txt
```

`verify.py` does not trust the report: it retrains from scratch, re-runs the demo and fresh held-out books with different seeds, recomputes F1 from the confusion matrix and memory savings from raw bytes, and only then compares with `results.json`. If `results.json` is missing (fresh clone), it regenerates it automatically.

### Minimal API example

```python
from gestalt import GestaltNet, TrainedScorer, Sentence

# A scorer can come from experiment.train(...). Here: three hand-set weights,
# enough to show all three boxes firing.
scorer = TrainedScorer({"OBJ:шрам": 4.0, "A:испу": 3.0, "A:спок": -3.0})
net = GestaltNet(scorer, verbose=True)

for i, text in enumerate([
    "Дворецкий достал белый платок.",
    "Он вытер платком лоб.",
    "После этого он спокойно убрал платок в карман.",   # closed -> Trash + trigger
    "Из-под манжеты выглянул шрам-полумесяц.",
    "Дворецкий прикрыл шрам рукавом.",
    "Он испуганно убежал, ничего не объяснив.",           # unresolved -> Dossier
    "На месте убийства нашли белый платок.",              # 🔔 trigger fires
], 1):
    net.read(Sentence(text, page=12, idx=i))
net.flush()

print(net.dossier)                       # {'Дворецкий': ['шрам-полумесяц (стр. 12)']}
print(net.beacons)                       # {'платок': [('Дворецкий', 12)]}
print(net.answer_by_prop("платок"))      # Дворецкий
```

---

## 6. Repository layout

```
README.md            this file
REDDIT_POST.txt      launch kit: titles, flairs, tags, copy-paste post body
LICENSE              MIT
results.json         recorded benchmark (all numbers above)
gestalt/
  __init__.py        package marker, re-exports the public API
  gestalt.py         the architecture: triad reader, three boxes, scorers
  experiment.py      dataset generator, SGD training, Butler demo, ablations, memory audit
  verify.py          27 independent checks; exit code 0 iff all pass
```

That's it. Four Python files, one JSON, three docs. Nothing else.

---

## 7. Ideas for extensions

- Replace templated stories with a real detective corpus and keep the same three boxes.
- Swap the linear scorer for a tiny local model as the "judge" of the triad.
- Age out triggers by recency; add a capacity budget and measure the accuracy/bytes frontier.
- Plug the Dossier into a real LLM's system prompt and measure actual token savings.

## License

MIT — see [LICENSE](LICENSE).
