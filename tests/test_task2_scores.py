from __future__ import annotations

import pytest

import task2_scores as scores
from common import build_session, get_json
from mock_scores_api import running_server

PAYLOAD = {
    "exam": "Unit test",
    "max_score": 100,
    "students": [
        {"id": 1, "name": "Asha", "scores": {"Maths": 80, "Physics": 70}},
        {"id": 2, "name": "Bilal", "scores": {"Maths": 90, "Physics": "AB"}},
        # Same name as student 1, but a different student.
        {"id": 3, "name": "Asha", "scores": {"Maths": 40, "Physics": 130}},
    ],
}


def test_unusable_scores_are_ignored():
    df, ignored = scores.to_frame(PAYLOAD)
    assert ignored == 2  # "AB" and 130
    assert sorted(df["score"]) == [40, 70, 80, 90]


def test_averages():
    s = scores.summarize(PAYLOAD)
    assert s.class_average == pytest.approx(70.0)  # (80 + 70 + 90 + 40) / 4
    assert s.n_students == 3
    # The two Ashas are averaged separately: 90 (Bilal), 75 (Asha #1), 40 (Asha #3).
    assert list(s.by_student.values) == pytest.approx([90.0, 75.0, 40.0])
    assert s.by_subject.to_dict() == pytest.approx({"Maths": 70.0, "Physics": 70.0})


@pytest.mark.parametrize(
    "bad", [[], {"students": "nope"}, {"students": [{"name": "X", "scores": {"M": "AB"}}]}]
)
def test_bad_payloads_raise(bad):
    with pytest.raises(ValueError):
        scores.summarize(bad)


def test_bundled_api_serves_the_dataset():
    with running_server() as url:
        payload = get_json(build_session("tests", retries=0), url)
    assert payload["max_score"] == 100
    assert len(payload["students"]) == 10


def test_cli_end_to_end_writes_a_png(tmp_path, capsys):
    out = tmp_path / "chart.png"
    assert scores.main(["--out", str(out)]) == 0
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    printed = capsys.readouterr().out
    assert "Class average: 75.44 / 100" in printed  # 2942 / 39 usable scores
    assert "39 scores from 10 students, 1 ignored" in printed


def test_cli_reports_an_unreachable_api(tmp_path):
    assert (
        scores.main(["--url", "http://127.0.0.1:9/api/students", "--out", str(tmp_path / "x.png")])
        == 1
    )
