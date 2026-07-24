"""A busy browser is a normal outcome, not a crash.

`smoke`, `cycle` and `login` all reach Chrome through provider.search(), so any of
them can lose the race for the profile. Every one has to say so in a line the user
can act on — the first cut of this guard only taught `login`, and `smoke` answered
a real collision with a BrowserBusy traceback.
"""

import pytest

from infinance import cli
from infinance.browser_lock import BrowserBusy


@pytest.mark.parametrize("command", ["login", "smoke", "cycle"])
def test_a_busy_browser_is_reported_not_raised(command, monkeypatch, capsys):
    def busy(*a, **kw):
        raise BrowserBusy("crawl", 4242)

    for name in ("cmd_login", "cmd_smoke", "cmd_cycle"):
        monkeypatch.setattr(cli, name, busy)

    code = cli.main([command])

    out = capsys.readouterr().out
    assert code == 1, "a busy browser is a failure, but a handled one"
    assert "already in use by a crawl (pid 4242)" in out  # names what holds it
    assert "Traceback" not in out
    assert "取消" in out  # and how to get it back
