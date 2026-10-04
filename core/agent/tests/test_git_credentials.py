"""Tests for git_credentials — the per-user, save-once reusable GitHub token store."""
from control_plane import git_credentials as gc


def test_set_read_mask_and_clear(tmp_path):
    root = tmp_path
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None

    # save → readable server-side, masked for display (never the clear value)
    assert gc.set_github_token(root, "42", "ghp_ABCDEFGH1234wxyz") is True
    assert gc.read_github_token(root, "42") == "ghp_ABCDEFGH1234wxyz"
    assert gc.masked_github_token(root, "42") == "••••wxyz"

    # stored under a dot-dir the workspace scanners skip, NOT inside a workspace tree
    f = root / ".secrets" / "42.ghtoken"
    assert f.exists()
    import os
    assert oct(f.stat().st_mode)[-3:] == "600"  # owner-only

    # per-subject isolation
    assert gc.read_github_token(root, "43") is None

    # clear
    assert gc.set_github_token(root, "42", "") is False
    assert gc.read_github_token(root, "42") is None
    assert gc.masked_github_token(root, "42") is None


def test_short_token_masks_without_leaking(tmp_path):
    gc.set_github_token(tmp_path, "1", "abcd")  # < 8 chars → mask shows no tail
    assert gc.masked_github_token(tmp_path, "1") == "••••"


def test_invalid_subject_rejected(tmp_path):
    assert gc._token_path(tmp_path, "../escape") is None
    assert gc._token_path(tmp_path, "") is None
    try:
        gc.set_github_token(tmp_path, "../escape", "x")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_a_copy_follows_the_token_it_was_copied_from(tmp_path):
    gc.set_github_token(tmp_path, "u_jane", "old")
    gc.copy_github_token(tmp_path, "u_jane", "product-repo", "old")
    assert gc.read_github_token(tmp_path, "product-repo") == "old"

    # reconnecting (or pasting) refreshes the copy
    gc.save_github_token(tmp_path, "u_jane", "new")
    assert gc.read_github_token(tmp_path, "u_jane") == "new"
    assert gc.read_github_token(tmp_path, "product-repo") == "new"


def test_a_save_by_someone_else_leaves_the_copy_alone(tmp_path):
    gc.copy_github_token(tmp_path, "u_jane", "product-repo", "janes")
    gc.save_github_token(tmp_path, "u_bob", "bobs")
    assert gc.read_github_token(tmp_path, "product-repo") == "janes"


def test_clearing_the_source_token_does_not_touch_the_copy(tmp_path):
    gc.copy_github_token(tmp_path, "u_jane", "product-repo", "janes")
    assert gc.save_github_token(tmp_path, "u_jane", "") is False
    assert gc.read_github_token(tmp_path, "product-repo") == "janes"


def test_a_copy_saved_directly_stops_following_its_source(tmp_path):
    gc.copy_github_token(tmp_path, "u_jane", "product-repo", "janes")
    gc.save_github_token(tmp_path, "product-repo", "its-own")
    gc.save_github_token(tmp_path, "u_jane", "janes-new")
    assert gc.read_github_token(tmp_path, "product-repo") == "its-own"


def test_the_source_record_is_not_a_token_and_follows_a_new_source(tmp_path):
    gc.copy_github_token(tmp_path, "u_jane", "product-repo", "janes")
    gc.copy_github_token(tmp_path, "u_bob", "product-repo", "bobs")
    gc.save_github_token(tmp_path, "u_jane", "janes-new")
    assert gc.read_github_token(tmp_path, "product-repo") == "bobs"
    gc.save_github_token(tmp_path, "u_bob", "bobs-new")
    assert gc.read_github_token(tmp_path, "product-repo") == "bobs-new"
