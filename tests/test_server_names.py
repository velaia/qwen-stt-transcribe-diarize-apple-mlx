"""Downloads name speakers exactly as the transcript view does."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.results import default_speaker_name
from tests.test_server_helpers import build_test_app, make_wav, upload, wait_for_status


def test_default_name_matches_the_frontend_rule():
    assert default_speaker_name("SPEAKER_00") == "Speaker 1"
    assert default_speaker_name("SPEAKER_07") == "Speaker 8"
    assert default_speaker_name("speaker_10") == "Speaker 11"
    assert default_speaker_name("Moderator") == "Moderator"


def test_unrenamed_speakers_get_their_display_name_in_downloads(tmp_path):
    """Regression: renaming one speaker left the other as a raw SPEAKER_01."""
    wav = make_wav(tmp_path / "m.wav", seconds=10.0)
    with TestClient(build_test_app(tmp_path / "data")) as client:
        item_id = upload(client, wav, options={"diarize": True}).json()["id"]
        wait_for_status(client, item_id)
        client.patch(f"/api/transcripts/{item_id}",
                     json={"speaker_names": {"SPEAKER_00": "Anna"}})

        for fmt in ("txt", "md", "srt", "vtt", "json"):
            text = client.get(f"/api/transcripts/{item_id}/download/{fmt}").text
            assert "Anna" in text, fmt
            assert "Speaker 2" in text, fmt
            assert "SPEAKER_0" not in text, fmt

        # The UI's own result endpoint keeps raw labels; names are applied client-side.
        result = client.get(f"/api/transcripts/{item_id}/result").json()
        assert result["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
