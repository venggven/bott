import pytest

from app.content import GREEN, RED, YELLOW, result_for_score


@pytest.mark.parametrize("score", [5, 6, 7, 8])
def test_green(score: int) -> None:
    assert result_for_score(score) is GREEN


@pytest.mark.parametrize("score", [9, 10, 11, 12])
def test_yellow(score: int) -> None:
    assert result_for_score(score) is YELLOW


@pytest.mark.parametrize("score", [13, 14, 15])
def test_red(score: int) -> None:
    assert result_for_score(score) is RED


@pytest.mark.parametrize("score", [0, 4, 16, 99])
def test_invalid_score(score: int) -> None:
    with pytest.raises(ValueError):
        result_for_score(score)

