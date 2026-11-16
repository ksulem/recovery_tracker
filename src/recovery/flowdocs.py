"""Generates docs/FLOWS.md: Mermaid diagrams of every check-in flow.

The question diagrams are built from ``conversation/flows.py`` itself, so they
can't drift from what the bot asks; ``tests/test_flow_docs.py`` fails if the
file is out of date. Regenerate after changing a flow:

    .venv/bin/python -m recovery.flowdocs > docs/FLOWS.md

How a check-in starts and the life of a parked thought are behaviour in code,
not data, so those two diagrams are written by hand below.
"""

from collections.abc import Iterator

from recovery.battery import FEATURE as BATTERY_FEATURE
from recovery.conversation.flows import (
    AD_HOC,
    EVENING,
    EVENING_SHORT,
    FEATURE_SETS,
    HYPOTHESES_FEATURE,
    MORNING,
    SLEEP_FEATURE,
    WALK_FEATURE,
    Flow,
    Kind,
    Question,
)

# Short names for the diagrams; every question id must be listed here.
SHORT = {
    "sleep": "Как ты после сна",
    "onset": "Как получилось заснуть",
    "onsetnote": "Что мешало заснуть (своими словами)",
    "presleep": "Что было перед сном",
    "psnote": "Перед сном: другое (своими словами)",
    "battery": "Заряд батарейки",
    "phys": "Физическая энергия 0–10",
    "desire": "Желание приятного",
    "outlook": "Как тяжело представлять день",
    "heavy": "Ожидается энергозатратное (текст)",
    "cmp": "По сравнению с утром",
    "oblig": "Сколько обязательств",
    "ep": "Что было заметного (эпизоды)",
    "epnote": "Эпизод «другое» (своими словами)",
    "rate": "Как это было: дало / забрало / оба / почти не повлияло",
    "gave": "Что дало",
    "gnote": "Что ещё дало (своими словами)",
    "took": "Что забрало",
    "tnote": "Что ещё забрало (своими словами)",
    "why": "По желанию или по необходимости",
    "preocc": "Что занимает мысли",
    "pnote": "Что именно занимает (текст, можно 📌 на сессию)",
    "energy": "Силы на приятное",
    "press": "Давление на себя",
    "pressnote": "Давление: другое (своими словами)",
    "more": "Подробнее?",
    "emo": "Эмоциональный ресурс 0–10",
    "soc": "Социальный ресурс 0–10",
    "cog": "Когнитивный ресурс 0–10",
    "all": "Общее самочувствие 0–10",
    "interest": "Интерес к делам 0–5",
    "signs": "Признаки перегрузки",
    "snote": "Признаки: другое (своими словами)",
    "tmrw": "Учесть завтра (текст)",
    "walk": "Мысль с прогулки: что с ней",
    "park": "Что ещё отложить на завтра (текст)",
    "note": "Опиши словами (текст)",
}

# Shorter names where a question is referred to inside a condition.
COND_NAMES = {"rate": "Как это было"}

FEATURE_CLASS = {
    BATTERY_FEATURE: "battery",
    HYPOTHESES_FEATURE: "hyp",
    SLEEP_FEATURE: "sleep",
    WALK_FEATURE: "walk",
}
CLASS_DEFS = (
    "  classDef battery fill:#ffe8cc,stroke:#d9480f,color:#000\n"
    "  classDef hyp fill:#f3d9fa,stroke:#862e9c,color:#000\n"
    "  classDef sleep fill:#d0ebff,stroke:#1864ab,color:#000\n"
    "  classDef walk fill:#d3f9d8,stroke:#2b8a3e,color:#000\n"
    "  classDef cond fill:#fff9db,stroke:#e67700,color:#000"
)


def _short_option(label: str) -> str:
    return label.split(" — ")[0]  # "Другое — опишу своими словами" → "Другое"


def _find(flow: Flow, qid: str) -> Question | None:
    base = qid.split(".")[0]
    return next((q for q in flow.questions if q.id == base), None)


def _node(q: Question) -> str:
    """Mermaid node: rectangle = one choice/scale, [[ ]] = several, [/ /] = free text."""
    text = f"{SHORT[q.id]} · {q.id}"
    if q.kind == Kind.MULTI:
        return f'q_{q.id}[["{text}"]]'
    if q.kind in (Kind.TEXT, Kind.PARK):
        return f'q_{q.id}[/"{text}"/]'
    return f'q_{q.id}["{text}"]'


def _condition(flow: Flow, q: Question) -> str | None:
    parts = []
    if q.when is not None:
        ref = _find(flow, q.when[0])
        assert ref is not None, q.when
        values = " / ".join(f"«{_short_option(ref.option_label(v))}»" for v in sorted(q.when[1]))
        verb = "выбрано" if ref.kind == Kind.MULTI else "ответ"
        parts.append(f"{COND_NAMES.get(ref.id, SHORT[ref.id])}: {verb} {values}")
    if q.items is not None and q.each is not None:
        source = _find(flow, q.each)
        assert source is not None
        names = " · ".join(_short_option(source.option_label(i)).lower() for i in sorted(q.items))
        parts.append(f"пункт — {names}")
    if q.every_days:
        parts.append(
            "через день (по чётным датам)" if q.every_days == 2 else f"раз в {q.every_days} дн."
        )
    if q.kind == Kind.THOUGHT:
        parts.append("сегодня брала мысль на прогулку")
    return " и ".join(parts) + "?" if parts else None


class _Chart:
    """Builds a top-down chart where every question leads to the next one
    (an answer or a skip — both move on)."""

    def __init__(self, flow: Flow) -> None:
        self.flow = flow
        self.lines: list[str] = []
        self.classes: dict[str, list[str]] = {}
        self.exits: list[tuple[str, str]] = []  # (node, edge label) waiting for the next node

    def link(self, target: str) -> None:
        for source, label in self.exits:
            arrow = f"-->|{label}|" if label else "-->"
            self.lines.append(f"  {source} {arrow} {target}")
        self.exits = []

    def question(self, q: Question) -> None:
        node = f"q_{q.id}"
        condition = _condition(self.flow, q)
        if condition:
            diamond = f"d_{q.id}"
            self.lines.append(f'  {diamond}{{"{condition}"}}')
            self.classes.setdefault("cond", []).append(diamond)
            self.link(diamond)
            self.lines.append(f"  {diamond} -->|да| {_node(q)}")
            self.exits = [(node, ""), (diamond, "нет")]
        else:
            self.lines.append(f"  {_node(q)}")
            self.link(node)
            self.exits = [(node, "")]
        if q.feature in FEATURE_CLASS:
            self.classes.setdefault(FEATURE_CLASS[q.feature], []).append(node)

    def render(self, end: str) -> str:
        self.lines.append(f'  done(["{end}"])')
        self.link("done")
        classes = [f"  class {','.join(nodes)} {name}" for name, nodes in self.classes.items()]
        return "\n".join(["flowchart TD", "  start([начало])", *self.lines, CLASS_DEFS, *classes])


def flow_chart(flow: Flow, end: str) -> str:
    chart = _Chart(flow)
    chart.exits = [("start", "")]
    questions = list(flow.questions)
    i = 0
    while i < len(questions):
        q = questions[i]
        if q.each is not None:
            # A per-item group collapses to one node here (drawn separately).
            group = [q]
            while i + len(group) < len(questions) and questions[i + len(group)].each == q.each:
                group.append(questions[i + len(group)])
            source = _find(flow, q.each)
            assert source is not None
            node = f"g_{q.each}"
            chart.lines.append(
                f'  {node}[["для каждого выбранного пункта, по порядку, '
                f'не больше {source.max_items}: вопросы ниже · {", ".join(t.id for t in group)}"]]'
            )
            chart.link(node)
            chart.exits = [(node, "")]
            i += len(group)
            continue
        if q.kind == Kind.GATE:
            chart.question(q)
            block = [b for b in questions[i + 1 :] if b.block == q.opens]
            gate = f"q_{q.id}"
            # "да" goes into the block; "нет" or a skip jumps over it.
            chart.exits = [(gate, "да")]
            for b in block:
                chart.question(b)
            chart.exits.append((gate, "нет / пропуск"))
            i += 1 + len(block)
            continue
        chart.question(q)
        i += 1
    return chart.render(end)


def item_chart(flow: Flow, source_id: str) -> str:
    """The questions asked for each selected item (e.g. each day episode)."""
    chart = _Chart(flow)
    chart.lines.append("  start([следующий выбранный пункт])")
    chart.exits = [("start", "")]
    for q in flow.questions:
        if q.each == source_id:
            chart.question(q)
    rendered = chart.render("к следующему пункту или дальше по опросу")
    return rendered.replace("  start([начало])\n", "", 1)


def _mermaid(code: str) -> str:
    return f"```mermaid\n{code}\n```"


START_DIAGRAM = """stateDiagram-v2
  state "Предложение по расписанию<br/>«Начать / Не сейчас»" as offer
  state "Проверка: что уже есть сегодня" as check
  state "Сегодняшний уже заполнен<br/>(предлагает /now)" as done_today
  state "Продолжить / Начать заново / Не сейчас" as resume
  state "Поздний вечер: «Коротко / Полный опрос / Не сейчас»" as late
  state "После полуночи: только отложить мысли" as night
  state "Опрос идёт (in_progress)" as active
  state "Закончен (completed)" as completed
  state "Брошен (abandoned)" as abandoned

  [*] --> offer: утро / вечер по расписанию,<br/>одно напоминание
  [*] --> check: /morning, /evening, /now
  offer --> check: «Начать»
  offer --> [*]: «Не сейчас» (напоминаний больше нет)
  check --> done_today: утро/вечер уже закончен
  check --> resume: утро/вечер начат и не закончен
  check --> late: /evening за час до сна или позже
  check --> night: /evening после полуночи
  check --> active: иначе (/now — всегда новый)
  late --> active: «Коротко» (заряд + отложить) или «Полный опрос»
  resume --> active: «Продолжить» — с первого неотвеченного
  resume --> abandoned: «Начать заново» — старый брошен
  abandoned --> active: новый опрос
  active --> active: ответ или «Пропустить»<br/>(каждый ответ сохраняется сразу)
  active --> completed: последний вопрос или «Закончить»
  completed --> [*]
  night --> [*]"""

THOUGHTS_DIAGRAM = """stateDiagram-v2
  state "Отложена (open)" as open
  state "Взята на прогулку сегодня" as taken
  state "Обдумано (done)" as done
  state "На сессию (to_session)" as session
  state "Неактуально (dropped)" as dropped

  [*] --> open: /later, «🅿️ На потом»,<br/>любое сообщение поздно вечером,<br/>закрытие дня в вечернем опросе
  open --> taken: перед прогулкой «Взять одну»<br/>(одна в день, «Другую» — следующая)
  taken --> open: «Пусть полежит» (счётчик +1)
  open --> open: «Пусть полежит» при разборе /later (счётчик +1)
  taken --> done: «Обдумано»
  taken --> session: «На сессию»
  taken --> dropped: «Неактуально»
  open --> done: разбор /later
  open --> session: разбор /later или предложение<br/>после 3-го «пусть полежит» (один раз)
  open --> dropped: разбор /later
  session --> [*]: пункт в списке для сессии
  done --> [*]
  dropped --> [*]"""

SCHEDULE = """| Время (пример) | Что делает бот | Откуда |
|---|---|---|
| утренний опрос, 09:30 | предложение утреннего опроса; напоминание через 2 ч, если не начат | `/settings` |
| вечерняя прогулка, 18:45 | только если что-то отложено: «взять одну мысль или просто погулять» | `/settings → 🐕` |
| вечерний опрос, 20:45 | предложение вечернего опроса; напоминание через 45 мин | `/settings` |
| за час до сна, 22:00 → +8 ч | поздний режим: без напоминаний, текст уходит «на потом», `/evening` — коротко | `/settings → 🌙` |
| за N часов до сессии | сводка к сессии | `/settings → Сессия` |"""


def _legend() -> str:
    sets = "\n".join(f"- **{label}** — {help_}" for label, help_ in FEATURE_SETS.values())
    return f"""Как читать диаграммы:

- Каждый вопрос можно **пропустить** — опрос просто идёт дальше. «**Закончить**»
  завершает опрос на любом вопросе. Каждый ответ сохраняется сразу.
- Форма: прямоугольник — один вариант или шкала; `[[двойная рамка]]` —
  можно выбрать несколько; параллелограмм — свободный текст.
- Жёлтый ромб — условие: вопрос задаётся, только если ответ «да».
- Цвет вопроса — от чего он зависит в настройках:
  оранжевый — 🔋 батарейка; фиолетовый — 🧩 вопросы для гипотез;
  голубой — 🧩 как получилось заснуть; зелёный — 🐕 время прогулки задано.
  Выключенное просто не задаётся.
- Подпись после «·» — id вопроса в `conversation/flows.py`.
- Текст, написанный во время вопроса с кнопками, сохраняется как заметка к опросу.
  Сообщение, похожее на кризисное, останавливает опрос (его можно продолжить потом).

Наборы дополнительных вопросов (`/settings → 🧩`):

{sets}"""


def render() -> str:
    sections: Iterator[str] = iter(
        [
            "# Как идут опросы",
            "_Этот файл собирается из кода: "
            "`.venv/bin/python -m recovery.flowdocs > docs/FLOWS.md`. "
            "Тест `tests/test_flow_docs.py` следит, чтобы он не устарел._",
            _legend(),
            "## Как начинается опрос",
            _mermaid(START_DIAGRAM),
            "## Утро (`/morning`)",
            _mermaid(flow_chart(MORNING, "Спасибо, записано")),
            "## Вечер (`/evening`)",
            _mermaid(flow_chart(EVENING, "День закрыт 🌙")),
            "### Вечер: вопросы по каждому выбранному эпизоду",
            "Задаются по очереди для первых трёх выбранных эпизодов "
            "(«Ничего особенного» — не эпизод).",
            _mermaid(item_chart(EVENING, "ep")),
            "## Поздний вечер: короткий вариант",
            "Когда `/evening` за час до сна или позже и выбрано «Коротко».",
            _mermaid(flow_chart(EVENING_SHORT, "День закрыт 🌙 Спокойной ночи")),
            "## Отметить состояние сейчас (`/now`)",
            _mermaid(flow_chart(AD_HOC, "Записано")),
            "## Отложенные мысли",
            _mermaid(THOUGHTS_DIAGRAM),
            "## Расписание дня",
            SCHEDULE,
        ]
    )
    return "\n\n".join(sections) + "\n"


if __name__ == "__main__":
    print(render(), end="")
