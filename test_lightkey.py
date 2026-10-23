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
                   lk.LightkeyState(lk.OPEN, "B"), lk.LightkeyState(lk.OPEN, "B"),
                   lk.LightkeyState(lk.PERMISSION)])
    seen = []
    w = lk.LightkeyWatcher(seen.append, interval=0, read=lambda: next(states))
    for _ in range(4):
        w.poll_once()
    assert [s.name for s in seen] == ["A", "B"]  # B only once it was seen twice
    w._run()  # next read is a permission refusal: reported once, then the watcher stops
    assert seen[-1].status == lk.PERMISSION


def windows(*rows):
    return "WINDOWS\n" + "".join("\t".join(r) + "\n" for r in rows)


SHOW = ("Club Night", "file:///Users/dj/Shows/Club%20Night.lightkey", "AXStandardWindow")


def test_panels_and_dialogs_in_front_are_not_projects():
    # External Control in front of the project window: still the project
    st = read(stdout=windows(("External Control", "", "AXFloatingWindow"), SHOW))
    assert (st.status, st.name) == (lk.OPEN, "Club Night")
    # Only panels / dialogs open: no project, never a project called "External Control"
    assert read(stdout=windows(("External Control", "", "AXStandardWindow"))).status == lk.NO_WINDOW
    assert read(stdout=windows(("Save", "", "AXDialog"))).status == lk.NO_WINDOW
    assert read(stdout=windows(("Preferences", "", "AXStandardWindow"))).status == lk.NO_WINDOW
    # Lightkey without file info: a normal window's title is still used
    assert read(stdout=windows(("Wedding — Edited", "", "AXStandardWindow"))).name == "Wedding"


def test_switching_windows_never_changes_the_project():
    """The bug: mapping a cue in Lightkey switched Lumière Link to another project."""
    show = lk.LightkeyState(lk.OPEN, "Club Night", "/Users/dj/Shows/Club Night.lightkey")
    reads = iter([show,
                  lk.LightkeyState(lk.OPEN, "External Control"),   # a title-only window in front
                  lk.LightkeyState(lk.OPEN, "External Control"),
                  lk.LightkeyState(lk.NO_WINDOW),                  # one odd poll
                  show, show])
    seen = []
    w = lk.LightkeyWatcher(seen.append, interval=0, read=lambda: next(reads))
    for _ in range(6):
        w.poll_once()
    assert [s.name for s in seen] == ["Club Night"]


def test_a_real_new_project_file_still_switches():
    a = lk.LightkeyState(lk.OPEN, "Club Night", "/Shows/Club Night.lightkey")
    b = lk.LightkeyState(lk.OPEN, "Wedding", "/Shows/Wedding.lightkey")
    reads = iter([a, b, b])
    seen = []
    w = lk.LightkeyWatcher(seen.append, interval=0, read=lambda: next(reads))
    for _ in range(3):
        w.poll_once()
    assert [s.name for s in seen] == ["Club Night", "Wedding"]
