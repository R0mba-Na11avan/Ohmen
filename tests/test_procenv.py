import sys

from pcb_assistant.procenv import external_env


def test_unfrozen_env_untouched(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = {"LD_LIBRARY_PATH": "/bundle", "PATH": "/usr/bin"}
    assert external_env(env) == env


def test_frozen_restores_original_library_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    env = {"LD_LIBRARY_PATH": "/opt/Ohmen/_internal", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib"}
    assert external_env(env) == {"LD_LIBRARY_PATH": "/usr/local/lib"}


def test_frozen_drops_library_path_the_user_never_set(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert external_env({"LD_LIBRARY_PATH": "/opt/Ohmen/_internal", "HOME": "/h"}) == {"HOME": "/h"}
