import runner


def test_run_once_swallows_failure_and_reports(capsys, monkeypatch):
    def exploding_main(engine):
        raise RuntimeError("excel could not be opened")

    monkeypatch.setattr(runner, "main", exploding_main)

    result = runner.run_once("rpabrowser_js")

    assert result is None
    captured = capsys.readouterr()
    assert "continuing with the next round" in captured.out
    assert "excel could not be opened" in captured.err


def test_main_forever_continues_after_failed_round(monkeypatch):
    calls = []

    def flaky_main(engine):
        calls.append(engine)
        if len(calls) == 1:
            raise RuntimeError("first round fails")
        raise SystemExit  # stop the loop once the second round is reached

    monkeypatch.setattr(runner, "main", flaky_main)
    monkeypatch.setattr(runner, "sleep_between_rounds", lambda seconds=0: None)

    try:
        runner.main_forever("rpabrowser_js")
    except SystemExit:
        pass

    assert calls == ["rpabrowser_js", "rpabrowser_js"]
