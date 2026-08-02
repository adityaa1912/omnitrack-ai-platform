from __future__ import annotations

import sys
import time
import types

import numpy as np
import pytest

from backend.settings import Settings
from inference.config import DetectorConfig


def test_openvino_provider_is_registered() -> None:
    from backend.inference.providers import OpenVINOProvider

    assert OpenVINOProvider.name == "openvino"


def test_tensorrt_provider_is_registered() -> None:
    from backend.inference.providers import TensorRTProvider

    assert TensorRTProvider.name == "tensorrt"


def test_settings_accepts_all_providers() -> None:
    for provider in ("torch", "onnx", "openvino", "tensorrt", "auto"):
        assert Settings(_env_file=None, inference_provider=provider).inference_provider == provider


def test_settings_rejects_unknown_provider() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, inference_provider="nope")


def test_build_provider_selects_by_name() -> None:
    from inference.detector import _build_provider

    torch_provider = _build_provider(DetectorConfig(inference_provider="torch"))
    assert torch_provider.name == "torch"

    onnx_provider = _build_provider(DetectorConfig(inference_provider="onnx"))
    assert onnx_provider.name == "onnx"

    openvino_provider = _build_provider(DetectorConfig(inference_provider="openvino"))
    assert openvino_provider.name == "openvino"

    tensorrt_provider = _build_provider(DetectorConfig(inference_provider="tensorrt"))
    assert tensorrt_provider.name == "tensorrt"


def test_build_provider_rejects_unknown() -> None:
    from inference.detector import _build_provider

    with pytest.raises(ValueError):
        _build_provider(DetectorConfig(inference_provider="nope"))


def test_openvino_availability_false_without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.inference.providers import openvino_provider

    monkeypatch.setattr(openvino_provider, "ov", None)
    assert openvino_provider.is_available() is False


def test_tensorrt_availability_false_without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.inference.providers import tensorrt_provider

    monkeypatch.setattr(tensorrt_provider, "ort", None)
    assert tensorrt_provider.is_available() is False


def test_settings_accepts_benchmark_knobs() -> None:
    s = Settings(_env_file=None, inference_benchmark_enabled=True, inference_benchmark_runs=3)
    assert s.inference_benchmark_enabled is True
    assert s.inference_benchmark_runs == 3


def _fake_provider(name: str, latency_ms: float):
    class _P:
        def __init__(self, config) -> None:
            self.config = config

        def load(self) -> None:
            pass

        def warmup(self) -> None:
            pass

        def predict(self, data):
            time.sleep(latency_ms / 1000.0)
            return []

    _P.name = name
    return _P


def test_benchmark_selects_fastest_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from backend.inference import benchmark

    model = tmp_path / "model.pt"
    model.write_bytes(b"x")
    config = DetectorConfig(
        model_name=str(model), inference_provider="auto", benchmark_runs=1
    )

    fast = _fake_provider("torch", 1.0)
    slow = _fake_provider("onnx", 30.0)

    provider = benchmark.select_provider(config, [slow, fast])
    assert provider.name == "torch"
    assert provider._auto_loaded is True
    assert benchmark.LAST_SELECTED == "torch"
    assert set(benchmark.LAST_RESULTS) == {"torch", "onnx"}


def test_benchmark_cache_reuse(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from backend.inference import benchmark

    model = tmp_path / "model.pt"
    model.write_bytes(b"x")
    config = DetectorConfig(
        model_name=str(model), inference_provider="auto", benchmark_runs=1
    )

    fast = _fake_provider("torch", 1.0)
    slow = _fake_provider("onnx", 30.0)

    benchmark.select_provider(config, [slow, fast])
    assert benchmark.cached_results(str(model))

    predicted: list[str] = []

    def _tracked(name: str, latency_ms: float):
        base = _fake_provider(name, latency_ms)
        original = base.predict

        def predict(self, data):
            predicted.append(name)
            return original(self, data)

        base.predict = predict
        return base

    benchmark.select_provider(
        config, [_tracked("onnx", 30.0), _tracked("torch", 1.0)]
    )
    assert predicted == []


def test_detector_runtime_fallback_rebuilds_provider() -> None:
    from inference.detector import Detector

    class _Broken:
        name = "broken"

        def predict(self, data):
            raise RuntimeError("device fault")

    rebuilt: list[bool] = []

    class _OK:
        name = "torch"

        def predict(self, data):
            return []

    detector = Detector.__new__(Detector)
    detector.provider = _Broken()

    def _rebuild() -> bool:
        detector.provider = _OK()
        rebuilt.append(True)
        return True

    detector._rebuild_fallback_provider = _rebuild
    assert detector._predict_with_fallback(np.zeros((2, 2, 3), dtype=np.uint8)) == []
    assert rebuilt == [True]


def test_auto_provider_falls_back_to_torch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.inference.providers import openvino_provider, tensorrt_provider
    import inference.detector as detector

    monkeypatch.setattr(openvino_provider, "is_available", lambda: False)
    monkeypatch.setattr(tensorrt_provider, "is_available", lambda: False)

    loaded: list[str] = []

    class _FailONNX:
        name = "onnx"

        def __init__(self, config) -> None:
            self.config = config

        def load(self) -> None:
            loaded.append("onnx")
            raise RuntimeError("no onnxruntime")

    class _OKTorch:
        name = "torch"

        def __init__(self, config) -> None:
            self.config = config

        def load(self) -> None:
            loaded.append("torch")

    fake_pkg = types.SimpleNamespace(
        ONNXProvider=_FailONNX,
        OpenVINOProvider=object,
        TensorRTProvider=object,
        TorchProvider=_OKTorch,
        openvino_provider=openvino_provider,
        tensorrt_provider=tensorrt_provider,
    )
    monkeypatch.setitem(sys.modules, "backend.inference.providers", fake_pkg)

    provider = detector._build_auto_provider(DetectorConfig(inference_provider="auto"))
    assert provider.name == "torch"
    assert provider._auto_loaded is True
    assert loaded == ["onnx", "torch"]
