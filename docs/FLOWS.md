# Как идут опросы

_Этот файл собирается из кода: `.venv/bin/python -m recovery.flowdocs > docs/FLOWS.md`. Тест `tests/test_flow_docs.py` следит, чтобы он не устарел._

Как читать диаграммы:

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

- **Вопросы для проверки гипотез** — вечером: сколько было обязательств, что занимает мысли, по желанию или по необходимости было общение и быт, давление на себя (через вечер)
- **Как получилось заснуть** — утром: засыпание и что было перед сном

## Как начинается опрос

```mermaid
stateDiagram-v2
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
  night --> [*]
```

## Утро (`/morning`)

```mermaid
flowchart TD
  start([начало])
  q_sleep["Как ты после сна · sleep"]
  start --> q_sleep
  q_onset["Как получилось заснуть · onset"]
  q_sleep --> q_onset
  d_onsetnote{"Как получилось заснуть: ответ «Трудно по другой причине»?"}
  q_onset --> d_onsetnote
  d_onsetnote -->|да| q_onsetnote[/"Что мешало заснуть (своими словами) · onsetnote"/]
  q_presleep[["Что было перед сном · presleep"]]
  q_onsetnote --> q_presleep
  d_onsetnote -->|нет| q_presleep
  d_psnote{"Что было перед сном: выбрано «Другое»?"}
  q_presleep --> d_psnote
  d_psnote -->|да| q_psnote[/"Перед сном: другое (своими словами) · psnote"/]
  q_battery["Заряд батарейки · battery"]
  q_psnote --> q_battery
  d_psnote -->|нет| q_battery
  q_phys["Физическая энергия 0–10 · phys"]
  q_battery --> q_phys
  q_desire["Желание приятного · desire"]
  q_phys --> q_desire
  q_outlook["Как тяжело представлять день · outlook"]
  q_desire --> q_outlook
  q_heavy[/"Ожидается энергозатратное (текст) · heavy"/]
  q_outlook --> q_heavy
  done(["Спасибо, записано"])
  q_heavy --> done
  classDef battery fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef hyp fill:#f3d9fa,stroke:#862e9c,color:#000
  classDef sleep fill:#d0ebff,stroke:#1864ab,color:#000
  classDef walk fill:#d3f9d8,stroke:#2b8a3e,color:#000
  classDef cond fill:#fff9db,stroke:#e67700,color:#000
  class q_onset,q_onsetnote,q_presleep,q_psnote sleep
  class d_onsetnote,d_psnote cond
  class q_battery battery
```

## Вечер (`/evening`)

```mermaid
flowchart TD
  start([начало])
  q_cmp["По сравнению с утром · cmp"]
  start --> q_cmp
  q_battery["Заряд батарейки · battery"]
  q_cmp --> q_battery
  q_oblig["Сколько обязательств · oblig"]
  q_battery --> q_oblig
  q_ep[["Что было заметного (эпизоды) · ep"]]
  q_oblig --> q_ep
  d_epnote{"Что было заметного (эпизоды): выбрано «Другое»?"}
  q_ep --> d_epnote
  d_epnote -->|да| q_epnote[/"Эпизод «другое» (своими словами) · epnote"/]
  g_ep[["для каждого выбранного пункта, по порядку, не больше 3: вопросы ниже · rate, gave, gnote, took, tnote, why"]]
  q_epnote --> g_ep
  d_epnote -->|нет| g_ep
  q_preocc["Что занимает мысли · preocc"]
  g_ep --> q_preocc
  d_pnote{"Что занимает мысли: ответ «Немного» / «Да, сильно»?"}
  q_preocc --> d_pnote
  d_pnote -->|да| q_pnote[/"Что именно занимает (текст, можно 📌 на сессию) · pnote"/]
  q_energy["Силы на приятное · energy"]
  q_pnote --> q_energy
  d_pnote -->|нет| q_energy
  d_press{"через день (по чётным датам)?"}
  q_energy --> d_press
  d_press -->|да| q_press[["Давление на себя · press"]]
  d_pressnote{"Давление на себя: выбрано «Другое»?"}
  q_press --> d_pressnote
  d_press -->|нет| d_pressnote
  d_pressnote -->|да| q_pressnote[/"Давление: другое (своими словами) · pressnote"/]
  q_more["Подробнее? · more"]
  q_pressnote --> q_more
  d_pressnote -->|нет| q_more
  q_phys["Физическая энергия 0–10 · phys"]
  q_more -->|да| q_phys
  q_emo["Эмоциональный ресурс 0–10 · emo"]
  q_phys --> q_emo
  q_soc["Социальный ресурс 0–10 · soc"]
  q_emo --> q_soc
  q_cog["Когнитивный ресурс 0–10 · cog"]
  q_soc --> q_cog
  q_all["Общее самочувствие 0–10 · all"]
  q_cog --> q_all
  q_interest["Интерес к делам 0–5 · interest"]
  q_all --> q_interest
  q_signs[["Признаки перегрузки · signs"]]
  q_interest --> q_signs
  d_snote{"Признаки перегрузки: выбрано «Другое»?"}
  q_signs --> d_snote
  d_snote -->|да| q_snote[/"Признаки: другое (своими словами) · snote"/]
  q_tmrw[/"Учесть завтра (текст) · tmrw"/]
  q_snote --> q_tmrw
  d_snote -->|нет| q_tmrw
  q_more -->|нет / пропуск| q_tmrw
  d_walk{"сегодня брала мысль на прогулку?"}
  q_tmrw --> d_walk
  d_walk -->|да| q_walk["Мысль с прогулки: что с ней · walk"]
  q_park[/"Что ещё отложить на завтра (текст) · park"/]
  q_walk --> q_park
  d_walk -->|нет| q_park
  done(["День закрыт 🌙"])
  q_park --> done
  classDef battery fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef hyp fill:#f3d9fa,stroke:#862e9c,color:#000
  classDef sleep fill:#d0ebff,stroke:#1864ab,color:#000
  classDef walk fill:#d3f9d8,stroke:#2b8a3e,color:#000
  classDef cond fill:#fff9db,stroke:#e67700,color:#000
  class q_battery battery
  class q_oblig,q_preocc,q_pnote,q_press,q_pressnote hyp
  class d_epnote,d_pnote,d_press,d_pressnote,d_snote,d_walk cond
  class q_walk walk
```

### Вечер: вопросы по каждому выбранному эпизоду

Задаются по очереди для первых трёх выбранных эпизодов («Ничего особенного» — не эпизод).

```mermaid
flowchart TD
  start([следующий выбранный пункт])
  q_rate["Как это было: дало / забрало / оба / почти не повлияло · rate"]
  start --> q_rate
  d_gave{"Как это было: ответ «И то, и другое»?"}
  q_rate --> d_gave
  d_gave -->|да| q_gave[["Что дало · gave"]]
  d_gnote{"Что дало: выбрано «Другое»?"}
  q_gave --> d_gnote
  d_gave -->|нет| d_gnote
  d_gnote -->|да| q_gnote[/"Что ещё дало (своими словами) · gnote"/]
  d_took{"Как это было: ответ «И то, и другое»?"}
  q_gnote --> d_took
  d_gnote -->|нет| d_took
  d_took -->|да| q_took[["Что забрало · took"]]
  d_tnote{"Что забрало: выбрано «Другое»?"}
  q_took --> d_tnote
  d_took -->|нет| d_tnote
  d_tnote -->|да| q_tnote[/"Что ещё забрало (своими словами) · tnote"/]
  d_why{"пункт — быт · общение с близкими · много людей, новые люди · общение по работе или обязательное?"}
  q_tnote --> d_why
  d_tnote -->|нет| d_why
  d_why -->|да| q_why["По желанию или по необходимости · why"]
  done(["к следующему пункту или дальше по опросу"])
  q_why --> done
  d_why -->|нет| done
  classDef battery fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef hyp fill:#f3d9fa,stroke:#862e9c,color:#000
  classDef sleep fill:#d0ebff,stroke:#1864ab,color:#000
  classDef walk fill:#d3f9d8,stroke:#2b8a3e,color:#000
  classDef cond fill:#fff9db,stroke:#e67700,color:#000
  class d_gave,d_gnote,d_took,d_tnote,d_why cond
  class q_why hyp
```

## Поздний вечер: короткий вариант

Когда `/evening` за час до сна или позже и выбрано «Коротко».

```mermaid
flowchart TD
  start([начало])
  q_battery["Заряд батарейки · battery"]
  start --> q_battery
  q_park[/"Что ещё отложить на завтра (текст) · park"/]
  q_battery --> q_park
  done(["День закрыт 🌙 Спокойной ночи"])
  q_park --> done
  classDef battery fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef hyp fill:#f3d9fa,stroke:#862e9c,color:#000
  classDef sleep fill:#d0ebff,stroke:#1864ab,color:#000
  classDef walk fill:#d3f9d8,stroke:#2b8a3e,color:#000
  classDef cond fill:#fff9db,stroke:#e67700,color:#000
  class q_battery battery
```

## Отметить состояние сейчас (`/now`)

```mermaid
flowchart TD
  start([начало])
  q_battery["Заряд батарейки · battery"]
  start --> q_battery
  q_all["Общее самочувствие 0–10 · all"]
  q_battery --> q_all
  q_signs[["Признаки перегрузки · signs"]]
  q_all --> q_signs
  d_snote{"Признаки перегрузки: выбрано «Другое»?"}
  q_signs --> d_snote
  d_snote -->|да| q_snote[/"Признаки: другое (своими словами) · snote"/]
  q_note[/"Опиши словами (текст) · note"/]
  q_snote --> q_note
  d_snote -->|нет| q_note
  done(["Записано"])
  q_note --> done
  classDef battery fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef hyp fill:#f3d9fa,stroke:#862e9c,color:#000
  classDef sleep fill:#d0ebff,stroke:#1864ab,color:#000
  classDef walk fill:#d3f9d8,stroke:#2b8a3e,color:#000
  classDef cond fill:#fff9db,stroke:#e67700,color:#000
  class q_battery battery
  class d_snote cond
```

## Отложенные мысли

```mermaid
stateDiagram-v2
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
  dropped --> [*]
```

## Расписание дня

| Время (пример) | Что делает бот | Откуда |
|---|---|---|
| утренний опрос, 09:30 | предложение утреннего опроса; напоминание через 2 ч, если не начат | `/settings` |
| вечерняя прогулка, 18:45 | только если что-то отложено: «взять одну мысль или просто погулять» | `/settings → 🐕` |
| вечерний опрос, 20:45 | предложение вечернего опроса; напоминание через 45 мин | `/settings` |
| за час до сна, 22:00 → +8 ч | поздний режим: без напоминаний, текст уходит «на потом», `/evening` — коротко | `/settings → 🌙` |
| за N часов до сессии | сводка к сессии | `/settings → Сессия` |
