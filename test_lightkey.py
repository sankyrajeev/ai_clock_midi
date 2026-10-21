import subprocess

import lightkey as lk


def fake(stdout="", stderr="", code=0):
    return lambda script: subprocess.CompletedProcess(["osascript"], code, stdout, stderr)


def read(**kw):
    return lk.read_open_project(run=fake(**kw), platform="darwin")


def test_open_project_with_file_path():
    st = read(stdout="Club Night.lightkey — Edited\nfile:///Users/dj/Documents/Shows/Club%20Night.lightkey\n")
    assert (st.status, st.name, st.path) == (lk.OPEN, "Club Night", "/Users/dj/Documents/Shows/Club Night.lightkey")


def test_open_project_title_only():
    assert read(stdout="Wedding — Edited\n\n").name == "Wedding"
    assert read(stdout="Wedding\nmissing value\n").name == "Wedding"
    assert read(stdout="Summer Tour.lightkey\n").name == "Summer Tour"


def test_not_running_no_window_and_errors():
    assert read(stdout="NOT_RUNNING\n").status == lk.NOT_RUNNING
    assert read(stdout="NO_WINDOW\n").status == lk.NO_WINDOW
    denied = read(code=1, stderr="execution error: Not authorized to send Apple events to System Events. (-1743)")
    assert denied.status == lk.PERMISSION
    assert read(code=1, stderr="System Events got an error: osascript is not allowed assistive access. (-25211)").status == lk.PERMISSION
    assert read(code=1, stderr="something else").status == lk.ERROR
    assert lk.read_open_project(run=fake(), platform="linux").status == lk.UNSUPPORTED


def test_clean_name():
    assert lk.clean_name("Club Night.lightkey — Edited") == "Club Night"
    assert lk.clean_name("Club Night - Edited") == "Club Night"
    assert lk.clean_name("  Main Stage  ") == "Main Stage"


def test_watcher_reports_changes_only_and_stops_on_permission():
    states = iter([lk.LightkeyState(lk.OPEN, "A"), lk.LightkeyState(lk.OPEN, "A"),
                   lk.LightkeyState(lk.OPEN, "B"), lk.LightkeyState(lk.PERMISSION)])
    seen = []
    w = lk.LightkeyWatcher(seen.append, interval=0, read=lambda: next(states))
    for _ in range(3):
        w.poll_once()
    assert [s.name for s in seen] == ["A", "B"]
    w._run()  # next read is a permission refusal: reported once, then the watcher stops
    assert seen[-1].status == lk.PERMISSION
