"""Tests for the `vertex` backend (Google Vertex AI via native google-genai SDK).

The google-genai SDK is an optional extra, so the success-path tests inject a
fake `google.genai` module into sys.modules; no live GCP call is made.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from graphify import llm

_PAYLOAD = {
    "nodes": [
        {"id": "svc_login", "label": "login", "file_type": "code", "source_file": "svc.py"},
        {"id": "svc_validate", "label": "validate", "file_type": "code", "source_file": "svc.py"},
    ],
    "edges": [
        {"source": "svc_login", "target": "svc_validate",
         "relation": "calls", "confidence": "EXTRACTED", "confidence_score": 1.0},
    ],
    "hyperedges": [],
}

# Env vars that would otherwise steer detect_backend away from vertex.
_STEERING_ENV = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "AWS_PROFILE", "AWS_REGION",
    "AWS_DEFAULT_REGION", "OLLAMA_BASE_URL", "GOOGLE_GENAI_USE_VERTEXAI",
)


def _clear_steering(monkeypatch):
    for k in _STEERING_ENV:
        monkeypatch.delenv(k, raising=False)


def _fake_response(text: str, *, prompt_tokens=12, out_tokens=8, finish="STOP"):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens, candidates_token_count=out_tokens
        ),
        candidates=[SimpleNamespace(finish_reason=finish)],
    )


@pytest.fixture
def fake_genai(monkeypatch):
    """Inject a fake `google.genai` so `_call_vertex` imports succeed."""
    monkeypatch.setattr(llm, "_response_is_hollow", lambda raw, parsed: False)

    generate = MagicMock(return_value=_fake_response(json.dumps(_PAYLOAD)))
    client = MagicMock()
    client.models.generate_content = generate
    client_cls = MagicMock(return_value=client)

    genai_mod = SimpleNamespace(Client=client_cls)
    types_mod = SimpleNamespace(GenerateContentConfig=lambda **kw: SimpleNamespace(**kw))
    genai_mod.types = types_mod
    google_pkg = SimpleNamespace(genai=genai_mod)

    with patch.dict(sys.modules, {
        "google": google_pkg,
        "google.genai": genai_mod,
        "google.genai.types": types_mod,
    }):
        yield SimpleNamespace(generate=generate, client_cls=client_cls)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_backend_registered():
    assert "vertex" in llm.BACKENDS
    cfg = llm.BACKENDS["vertex"]
    assert cfg["default_model"] == "gemini-2.5-flash"
    assert cfg["model_env_key"] == "GRAPHIFY_VERTEX_MODEL"
    # No static-key config — auth is via ADC.
    assert "env_key" not in cfg and "env_keys" not in cfg
    assert llm.estimate_cost("vertex", 1_000_000, 1_000_000) == pytest.approx(0.30 + 2.50)


# ---------------------------------------------------------------------------
# Detection (no shadowing of paid keys)
# ---------------------------------------------------------------------------


def test_detect_vertex_via_use_vertexai_flag(monkeypatch):
    _clear_steering(monkeypatch)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    assert llm.detect_backend() == "vertex"


def test_bare_project_does_not_autodetect_vertex(monkeypatch):
    _clear_steering(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")  # common in any GCP shell
    # Without the explicit flag, auto-detect must NOT pick vertex.
    assert llm.detect_backend() != "vertex"


def test_gemini_api_key_beats_vertex(monkeypatch):
    _clear_steering(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    assert llm.detect_backend() == "gemini"


# ---------------------------------------------------------------------------
# _call_vertex
# ---------------------------------------------------------------------------


def test_call_vertex_parses_response(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    result = llm._call_vertex("gemini-2.5-flash", "dummy", max_tokens=8192)
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    assert result["input_tokens"] == 12
    assert result["output_tokens"] == 8
    assert result["model"] == "gemini-2.5-flash"
    assert result["finish_reason"] == "stop"
    # vertexai=True + project/location threaded into the client
    _, kwargs = fake_genai.client_cls.call_args
    assert kwargs["vertexai"] is True
    assert kwargs["project"] == "my-proj"
    assert kwargs["location"] == "us-central1"


def test_call_vertex_max_tokens_maps_to_length(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    fake_genai.generate.return_value = _fake_response(
        json.dumps(_PAYLOAD), finish="MAX_TOKENS"
    )
    result = llm._call_vertex("gemini-2.5-flash", "dummy")
    assert result["finish_reason"] == "length"


def test_call_vertex_defaults_location_and_accepts_alias(monkeypatch, fake_genai):
    _clear_steering(monkeypatch)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("VERTEX_LOCATION", raising=False)
    monkeypatch.setenv("VERTEX_PROJECT", "alias-proj")  # alias accepted
    llm._call_vertex("gemini-2.5-flash", "dummy")
    _, kwargs = fake_genai.client_cls.call_args
    assert kwargs["project"] == "alias-proj"
    assert kwargs["location"] == "us-central1"  # default region


def test_call_vertex_requires_project(monkeypatch, fake_genai):
    _clear_steering(monkeypatch)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_PROJECT", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        llm._call_vertex("gemini-2.5-flash", "dummy")


def test_call_vertex_missing_sdk_raises_importerror(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    # Force the SDK import to fail regardless of whether it is installed.
    with patch.dict(sys.modules, {"google.genai": None}):
        with pytest.raises(ImportError, match=r"graphifyy\[vertex\]"):
            llm._call_vertex("gemini-2.5-flash", "dummy")


# ---------------------------------------------------------------------------
# Dispatch through extract_files_direct
# ---------------------------------------------------------------------------


def test_extract_files_direct_dispatches_to_vertex(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    f = tmp_path / "svc.py"
    f.write_text("def login():\n    return validate()\n")
    with patch("graphify.llm._call_vertex", return_value=dict(_PAYLOAD, input_tokens=1, output_tokens=1)) as cv:
        result = llm.extract_files_direct(files=[f], backend="vertex", root=tmp_path)
    assert cv.called
    # default model resolved
    assert cv.call_args[0][0] == "gemini-2.5-flash"
    assert len(result["nodes"]) == 2


def test_vertex_model_override_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setenv("GRAPHIFY_VERTEX_MODEL", "gemini-2.5-pro")
    f = tmp_path / "svc.py"
    f.write_text("def login():\n    return 1\n")
    with patch("graphify.llm._call_vertex", return_value=dict(_PAYLOAD, input_tokens=1, output_tokens=1)) as cv:
        llm.extract_files_direct(files=[f], backend="vertex", root=tmp_path)
    assert cv.call_args[0][0] == "gemini-2.5-pro"


def test_extract_files_direct_no_key_exemption(tmp_path, monkeypatch):
    """vertex must NOT raise the 'No API key' ValueError (it uses ADC)."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    f = tmp_path / "svc.py"
    f.write_text("def login():\n    return 1\n")
    with patch("graphify.llm._call_vertex", return_value=dict(_PAYLOAD, input_tokens=0, output_tokens=0)):
        # Should not raise despite no static API key being set.
        llm.extract_files_direct(files=[f], backend="vertex", root=tmp_path)
