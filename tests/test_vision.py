"""Tests for the ELA document-authenticity vision layer.

Offline + deterministic: we synthesize clean vs spliced JPEGs and check the ELA
features + classifier separate them above chance. Requires the `vision` extra.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from relief_probe.vision.datasets import iter_labeled_images, make_synthetic
from relief_probe.vision.ela import FEATURE_NAMES, ela_features
from relief_probe.vision.model import forgery_probability, load_model, train


def test_ela_features_shape_and_determinism():
    rng = np.random.default_rng(0)
    arr = (rng.random((64, 64, 3)) * 255).astype("uint8")
    img = Image.fromarray(arr, "RGB")
    f1 = ela_features(img)
    f2 = ela_features(img)
    assert f1.shape == (len(FEATURE_NAMES),)
    assert np.allclose(f1, f2)  # deterministic
    assert np.all(np.isfinite(f1))


def test_make_synthetic_writes_both_classes(tmp_path):
    counts = make_synthetic(tmp_path, n_per_class=5, seed=1)
    assert counts == {"authentic": 5, "forged": 5}
    items = iter_labeled_images(tmp_path)
    assert len(items) == 10
    assert {lbl for _, lbl in items} == {0, 1}


def test_train_separates_clean_from_forged(tmp_path):
    data = tmp_path / "synth"
    make_synthetic(data, n_per_class=40, seed=2)
    summary = train(data, out_path=tmp_path / "m.joblib", seed=0)
    assert summary["n_images"] == 80
    # ELA should separate spliced (double-compressed patch) from clean well above chance.
    assert summary["cv_accuracy_mean"] > 0.7


def test_score_roundtrip(tmp_path):
    data = tmp_path / "synth"
    make_synthetic(data, n_per_class=30, seed=3)
    model_path = tmp_path / "m.joblib"
    train(data, out_path=model_path, seed=0)
    model = load_model(model_path)
    with Image.open(data / "forged" / "img_0000.jpg") as img:
        p = forgery_probability(model, img)
    assert 0.0 <= p <= 1.0


def test_train_needs_enough_images(tmp_path):
    (tmp_path / "authentic").mkdir()
    (tmp_path / "forged").mkdir()
    with pytest.raises(ValueError):
        train(tmp_path)


def test_synthetic_note_is_honest():
    from relief_probe.vision import SYNTHETIC_NOTE

    assert SYNTHETIC_NOTE
    assert "synthetic" in SYNTHETIC_NOTE.lower()


def test_vision_demo_cli_prints_synthetic_note(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from relief_probe import config
    from relief_probe.cli import app
    from relief_probe.vision import SYNTHETIC_NOTE

    # Keep the demo self-contained and off the real data dir.
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    result = CliRunner().invoke(app, ["vision-demo", "--n-per-class", "12"])
    assert result.exit_code == 0, result.output
    # Rich word-wraps the console output, so collapse whitespace before matching.
    normalized = " ".join(result.output.split())
    assert " ".join(SYNTHETIC_NOTE.split()) in normalized
