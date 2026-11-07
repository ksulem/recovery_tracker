import pytest

from recovery.safety import looks_like_crisis


@pytest.mark.parametrize(
    "text",
    [
        "Я больше не хочу жить",
        "иногда думаю покончить с собой",
        "Хочется умереть",
        "лучше бы меня не было",
        "не вижу смысла жить",
        "мысли о суициде",
        "хочу причинить себе вред",
        "Свести счёты с жизнью",
        "не хотела жить сегодня",
    ],
)
def test_detects_crisis_phrases(text: str) -> None:
    assert looks_like_crisis(text)


@pytest.mark.parametrize(
    "text",
    [
        "Очень устала, хочу спать",
        "Убила весь день на отчёт",
        "Хочу жить спокойнее",
        "Смерть как надоела эта работа",
        "Посмотрела сериал про врачей",
        "Не хочу никого видеть",
    ],
)
def test_ordinary_tiredness_is_not_flagged(text: str) -> None:
    assert not looks_like_crisis(text)
