"""Load the MIDI library once, before any test starts threads: two threads
importing mido for the first time at once can trip over each other."""

try:
    import mido  # noqa: F401
except ImportError:   # the factory tests don't need it
    pass
