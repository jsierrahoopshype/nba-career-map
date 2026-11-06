"""Offline tests for test_sample.py's server-error rule. No network.

429 / 5xx after every retry -> NOT TESTED (exit 0). Everything else that is
wrong -> FAIL (exit 1).

Run:  python3 scripts/test_sample_not_tested.py
"""
from __future__ import annotations

import io
import sys
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sample as ts  # noqa: E402

GOOD = {"status": "success", "position": "F", "number": [23],
        "birth_date": "1984-12-30",
        "career_history": [{"team": "Cleveland Cavaliers", "years": "2003–2010"}]}


def http(code, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return urllib.error.HTTPError("https://en.wikipedia.org/w/api.php", code,
                                  "x", headers, None)


class Fetch:
    """Serves wikitext per name; a name mapped to a list raises/returns each
    item in turn (the last one repeats)."""
    def __init__(self, plan):
        self.plan, self.calls = plan, {}

    def __call__(self, name):
        n = self.calls[name] = self.calls.get(name, 0) + 1
        step = self.plan[name]
        if isinstance(step, list):
            step = step[min(n, len(step)) - 1]
        if isinstance(step, BaseException):
            raise step
        return step


def run(plan, parse=lambda wt, name: dict(GOOD, career_history=list(GOOD["career_history"]))):
    sleeps = []
    out = io.StringIO()
    with redirect_stdout(out):
        code = ts.run(Fetch(plan), parse, names=list(plan), backoff=(1, 2, 4),
                      sleep=sleeps.append)
    return code, out.getvalue(), sleeps


def test_429_on_every_try_is_not_tested():
    code, out, sleeps = run({"A": http(429), "B": http(429)})
    assert code == 0, out
    assert ts.NOT_TESTED_SERVER in out and "FAIL" not in out, out
    assert sleeps == [1, 2, 4] * 2, f"backoff not applied: {sleeps}"
    print("test_429_on_every_try_is_not_tested PASS")


def test_5xx_is_not_tested_and_retry_after_is_honoured():
    code, out, sleeps = run({"A": http(503, retry_after=10)})
    assert code == 0 and ts.NOT_TESTED_SERVER in out, out
    assert sleeps == [10, 10, 10], sleeps
    print("test_5xx_is_not_tested_and_retry_after_is_honoured PASS")


def test_a_retry_that_succeeds_is_tested_normally():
    code, out, sleeps = run({"A": [http(429), "wikitext"]})
    assert code == 0 and "RESULT: PASS" in out and "NOT TESTED" not in out, out
    assert sleeps == [1], sleeps
    print("test_a_retry_that_succeeds_is_tested_normally PASS")


def test_partly_refused_still_passes_and_names_the_untested():
    code, out, _ = run({"A": "wikitext", "B": http(502)})
    assert code == 0, out
    assert "1 " + ts.NOT_TESTED_SERVER in out and "B" in out.splitlines()[-1], out
    print("test_partly_refused_still_passes_and_names_the_untested PASS")


def test_other_http_errors_still_fail():
    for code_ in (403, 404):
        try:
            run({"A": http(code_)})
        except urllib.error.HTTPError as exc:
            assert exc.code == code_
        else:
            raise AssertionError(f"HTTP {code_} must not be excused")
    print("test_other_http_errors_still_fail PASS")


def test_timeouts_and_crashes_still_fail():
    for exc in (urllib.error.URLError("timed out"), ValueError("bad json")):
        try:
            run({"A": exc})
        except type(exc):
            pass
        else:
            raise AssertionError(f"{exc!r} must not be excused")
    print("test_timeouts_and_crashes_still_fail PASS")


def test_missing_page_fails():
    code, out, _ = run({"A": None})
    assert code == 1 and "page not found" in out, out
    print("test_missing_page_fails PASS")


def test_wrong_data_fails_even_when_others_were_refused():
    bad = [
        {"status": "no_career_data", "career_history": []},
        {"status": "success", "career_history": []},
        {"status": "success", "career_history": [{"team": "", "years": "2003"}]},
    ]
    for rec in bad:
        code, out, _ = run({"A": "wikitext", "B": http(429)},
                           parse=lambda wt, name, rec=rec: dict(rec))
        assert code == 1 and "RESULT: FAIL" in out, (rec, out)
    print("test_wrong_data_fails_even_when_others_were_refused PASS")


def test_parse_errors_fail():
    def boom(wt, name):
        raise KeyError("infobox")
    try:
        run({"A": "wikitext"}, parse=boom)
    except KeyError:
        pass
    else:
        raise AssertionError("a parser crash must fail the test")
    print("test_parse_errors_fail PASS")


if __name__ == "__main__":
    test_429_on_every_try_is_not_tested()
    test_5xx_is_not_tested_and_retry_after_is_honoured()
    test_a_retry_that_succeeds_is_tested_normally()
    test_partly_refused_still_passes_and_names_the_untested()
    test_other_http_errors_still_fail()
    test_timeouts_and_crashes_still_fail()
    test_missing_page_fails()
    test_wrong_data_fails_even_when_others_were_refused()
    test_parse_errors_fail()
    print("\nall test_sample server-error tests PASS")
