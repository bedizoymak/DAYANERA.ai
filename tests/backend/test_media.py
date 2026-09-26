"""Audio transcription (local faster-whisper) and video cataloguing."""
from __future__ import annotations

import os
import subprocess

import numpy as np
import pytest

pytestmark = pytest.mark.slow


def _tts_wav(path, text: str) -> bool:
    if os.name != "nt":
        return False
    ps = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=120)
    return r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 1000


def test_audio_is_transcribed_locally_as_draft(admin, settings, process_jobs, tmp_path):
    if not (settings.whisper_model_dir / "model.bin").exists():
        pytest.skip("faster-whisper modeli yok (scripts/fetch-local-models.ps1)")
    wav = tmp_path / "ses.wav"
    if not _tts_wav(str(wav), "The gear module is two point five millimeters. The number of teeth is thirty one."):
        pytest.skip("Windows konuşma sentezi kullanılamıyor")
    doc_id = admin.upload("/attachments", "toplanti.wav", wav.read_bytes(), "audio/wav").json()["id"]
    process_jobs()
    doc = admin.get(f"/documents/{doc_id}").json()
    v = doc["current_version"]
    assert v["ingestion_status"] == "indexed", v
    pages = admin.get(f"/documents/{doc_id}/versions/{v['id']}/pages").json()
    assert pages[0]["extraction_method"] == "transcription"
    assert pages[0]["confidence_status"] == "draft_extraction"
    text = admin.get(f"/documents/{doc_id}/versions/{v['id']}/pages/1").json()["text"].lower()
    assert "module" in text or "gear" in text
    assert v["metadata"]["transcription"]["language"] == "en"


def _video(path, with_audio=False):
    import av

    container = av.open(str(path), mode="w")
    vs = container.add_stream("mpeg4", rate=10)
    vs.width, vs.height, vs.pix_fmt = 160, 120, "yuv420p"
    for i in range(30):
        img = np.zeros((120, 160, 3), dtype=np.uint8)
        img[:, : (i * 5) % 160] = (200, 30, 30)
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for pkt in vs.encode(frame):
            container.mux(pkt)
    for pkt in vs.encode():
        container.mux(pkt)
    container.close()


def test_video_is_archived_and_catalogued_with_frames(admin, process_jobs, tmp_path):
    mp4 = tmp_path / "tezgah.mp4"
    try:
        _video(mp4)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Video üretilemedi: {exc}")
    doc_id = admin.upload("/attachments", "tezgah.mp4", mp4.read_bytes(), "video/mp4").json()["id"]
    process_jobs()
    v = admin.get(f"/documents/{doc_id}").json()["current_version"]
    assert v["category"] == "video"
    assert v["ingestion_status"] == "stored_only"  # archived + catalogued, no heavy analysis
    assert v["metadata"]["duration_seconds"] == pytest.approx(3.0, abs=0.5)
    assert any(s["type"] == "video" and s["width"] == 160 for s in v["metadata"]["streams"])
    assert len(v["metadata"]["media"]) == 3
    assert "kataloglandı" in v["extraction_summary"]["stored_only_reason"]
    assert admin.get(f"/documents/{doc_id}/versions/{v['id']}/preview?page=2").status_code == 200
    # raw video bytes are preserved and downloadable
    dl = admin.get(f"/documents/{doc_id}/versions/{v['id']}/download")
    assert dl.status_code == 200 and dl.content == mp4.read_bytes()
