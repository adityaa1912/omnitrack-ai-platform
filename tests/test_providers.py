from __future__ import annotations

import sys
import types

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
        Settings(_env_file=None, inference_provider="tensorrt")


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


def test_auto_falls_back_to_torch_when_accelerators_absent(
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
