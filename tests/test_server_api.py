"""CRUD, search, tags, downloads, resmooth and caching -- no real model loaded."""

from __future__ import annotations

import gzip
import json

from fastapi.testclient import TestClient

from tests.test_server_helpers import build_test_app, make_wav, upload, wait_for_status


def _client(tmp_path):
    app = build_test_app(tmp_path / "data")
    return TestClient(app)


def test_upload_runs_job_to_done(tmp_path):
    wav = make_wav(tmp_path / "hello.wav", seconds=1.0)
    with _client(tmp_path) as client:
        resp = upload(client, wav, title="Hello Test")
        assert resp.status_code == 201
        summary = resp.json()
        assert summary["status"] == "queued"
        assert summary["title"] == "Hello Test"
        assert summary["source_filename"] == "hello.wav"

        final = wait_for_status(client, summary["id"])
        detail = final.json()
        assert detail["status"] == "done"
        assert detail["has_result"] is True
        assert detail["language"] == "English"

        result = client.get(f"/api/transcripts/{summary['id']}/result")
        assert result.status_code == 200
        body = result.json()
        assert body["segments"][0]["text"]


def test_silence_produces_no_segments(tmp_path):
    wav = make_wav(tmp_path / "silence.wav", seconds=1.0, silent=True)
    with _client(tmp_path) as client:
        resp = upload(client, wav)
        item_id = resp.json()["id"]
        final = wait_for_status(client, item_id).json()
        assert final["status"] == "done"

        result = client.get(f"/api/transcripts/{item_id}/result").json()
        assert result["segments"] == []


def test_list_and_search_with_umlaut(tmp_path):
    wav_en = make_wav(tmp_path / "a.wav", seconds=1.0)
    wav_de = make_wav(tmp_path / "b.wav", seconds=1.0)
    with _client(tmp_path) as client:
        r1 = upload(client, wav_en, title="English item", options={"language": "en"})
        r2 = upload(client, wav_de, title="German item", options={"language": "de"})
        id_en, id_de = r1.json()["id"], r2.json()["id"]
        wait_for_status(client, id_en)
        wait_for_status(client, id_de)

        listing = client.get("/api/transcripts").json()
        assert {t["id"] for t in listing} == {id_en, id_de}
        # newest first
        assert listing[0]["id"] == id_de

        # The German fake transcript's body contains "Grüße aus Berlin."
        for query in ("grüße", "grusse", "GRUSSE"):
            found = client.get("/api/transcripts", params={"q": query}).json()
            assert [t["id"] for t in found] == [id_de], f"query {query!r} failed"

        no_match = client.get("/api/transcripts", params={"q": "xyzxyz"}).json()
        assert no_match == []


def test_patch_title_notes_tags_and_speaker_names(tmp_path):
    wav = make_wav(tmp_path / "meeting.wav", seconds=2.0)
    with _client(tmp_path) as client:
        resp = upload(client, wav, options={"diarize": True, "num_speakers": 2})
        item_id = resp.json()["id"]
        wait_for_status(client, item_id)

        patched = client.patch(f"/api/transcripts/{item_id}", json={
            "title": "New Title",
            "notes": "some notes",
            "tags": ["  Meeting  ", "Meeting", "Standup"],
            "speaker_names": {"SPEAKER_00": "Alice", "SPEAKER_01": ""},
        })
        assert patched.status_code == 200
        detail = patched.json()
        assert detail["title"] == "New Title"
        assert detail["notes"] == "some notes"
        assert sorted(detail["tags"]) == ["meeting", "standup"]
        assert detail["speaker_names"] == {"SPEAKER_00": "Alice"}  # empty value dropped

        # unknown keys -> 422
        bad = client.patch(f"/api/transcripts/{item_id}", json={"bogus": 1})
        assert bad.status_code == 422


def test_tags_endpoint_counts(tmp_path):
    wav1 = make_wav(tmp_path / "a.wav", seconds=1.0)
    wav2 = make_wav(tmp_path / "b.wav", seconds=1.0)
    with _client(tmp_path) as client:
        id1 = upload(client, wav1).json()["id"]
        id2 = upload(client, wav2).json()["id"]
        wait_for_status(client, id1)
        wait_for_status(client, id2)
        client.patch(f"/api/transcripts/{id1}", json={"tags": ["work", "podcast"]})
        client.patch(f"/api/transcripts/{id2}", json={"tags": ["podcast"]})

        tags = client.get("/api/tags").json()
        by_tag = {t["tag"]: t["count"] for t in tags}
        assert by_tag["podcast"] == 2
        assert by_tag["work"] == 1


def test_downloads_apply_speaker_names(tmp_path):
    wav = make_wav(tmp_path / "meeting.wav", seconds=2.0)
    with _client(tmp_path) as client:
        resp = upload(client, wav, title="Team/Sync", options={"diarize": True, "num_speakers": 2})
        item_id = resp.json()["id"]
        wait_for_status(client, item_id)
        client.patch(f"/api/transcripts/{item_id}", json={
            "speaker_names": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        })

        for fmt in ("txt", "md", "srt", "vtt", "json"):
            r = client.get(f"/api/transcripts/{item_id}/download/{fmt}")
            assert r.status_code == 200, fmt
            assert "attachment" in r.headers["content-disposition"]
            assert "filename*=UTF-8''" in r.headers["content-disposition"]
            text = r.text
            assert "Alice" in text
            assert "Bob" in text
            assert "SPEAKER_00" not in text

        bad_fmt = client.get(f"/api/transcripts/{item_id}/download/exe")
        assert bad_fmt.status_code == 404


def test_resmooth_changes_segment_count_both_ways(tmp_path):
    wav = make_wav(tmp_path / "meeting.wav", seconds=10.0)
    with _client(tmp_path) as client:
        resp = upload(client, wav, options={"diarize": True})
        item_id = resp.json()["id"]
        wait_for_status(client, item_id)

        # Default smoothing (max_words=3, max_dur=1.2, max_gap=2.0): both
        # one-word interjections are absorbed, but the >2s gap around the
        # second one stops it merging into the surrounding speaker's turn.
        baseline = client.get(f"/api/transcripts/{item_id}/result").json()
        n0 = len(baseline["segments"])
        assert n0 == 3

        # More tolerant: the same absorption, plus merging across the big gap.
        merged = client.post(f"/api/transcripts/{item_id}/resmooth", json={
            "max_words": 10, "max_dur": 10, "max_gap": 30,
        })
        assert merged.status_code == 200
        n_merged = len(client.get(f"/api/transcripts/{item_id}/result").json()["segments"])
        assert n_merged == 2

        # No absorption or merging at all: back to the raw per-span segments.
        split = client.post(f"/api/transcripts/{item_id}/resmooth", json={
            "max_words": 0, "max_dur": 0, "max_gap": 0,
        })
        assert split.status_code == 200
        n_split = len(client.get(f"/api/transcripts/{item_id}/result").json()["segments"])
        assert n_split == 6

        assert n_merged < n0 < n_split


def test_invalid_ids_404(tmp_path):
    with _client(tmp_path) as client:
        for bad in ("../etc", "ABC", "not-10-chars", "x" * 10 + "!"):
            assert client.get(f"/api/transcripts/{bad}").status_code == 404
        assert client.get("/api/transcripts/0123456789").status_code == 404


def test_range_request_on_audio(tmp_path):
    wav = make_wav(tmp_path / "clip.wav", seconds=1.0)
    with _client(tmp_path) as client:
        item_id = upload(client, wav).json()["id"]

        def audio_ready():
            return client.get(f"/api/transcripts/{item_id}").json()["audio_ready"]

        from tests.test_server_helpers import wait_until
        assert wait_until(audio_ready, timeout=10.0)

        full = client.get(f"/api/transcripts/{item_id}/media/audio")
        assert full.status_code == 200
        total = len(full.content)
        assert total > 0

        ranged = client.get(
            f"/api/transcripts/{item_id}/media/audio", headers={"Range": "bytes=0-9"}
        )
        assert ranged.status_code == 206
        assert ranged.headers["content-range"] == f"bytes 0-9/{total}"
        assert len(ranged.content) == 10
        assert ranged.headers["cache-control"] == "private, max-age=31536000, immutable"


def test_result_endpoint_gzip_and_etag(tmp_path):
    wav = make_wav(tmp_path / "clip.wav", seconds=1.0)
    with _client(tmp_path) as client:
        item_id = upload(client, wav).json()["id"]
        wait_for_status(client, item_id)

        plain = client.get(f"/api/transcripts/{item_id}/result",
                            headers={"Accept-Encoding": "identity"})
        assert plain.status_code == 200
        etag = plain.headers["etag"]

        gz = client.get(f"/api/transcripts/{item_id}/result",
                         headers={"Accept-Encoding": "gzip"})
        assert gz.status_code == 200
        assert gz.headers["etag"] == etag

        cached = client.get(
            f"/api/transcripts/{item_id}/result",
            headers={"If-None-Match": etag, "Accept-Encoding": "identity"},
        )
        assert cached.status_code == 304


def test_result_gz_file_is_actually_gzip(tmp_path):
    wav = make_wav(tmp_path / "clip.wav", seconds=1.0)
    with _client(tmp_path) as client:
        item_id = upload(client, wav).json()["id"]
        wait_for_status(client, item_id)
    gz_path = tmp_path / "data" / "items" / item_id / "result.json.gz"
    assert gz_path.exists()
    data = gzip.decompress(gz_path.read_bytes())
    parsed = json.loads(data)
    assert "segments" in parsed


def test_options_and_health_endpoints(tmp_path):
    with _client(tmp_path) as client:
        opts = client.get("/api/options").json()
        assert opts["defaults"]["language"] == "auto"
        assert "vad_threshold" in opts["limits"]

        health = client.get("/api/health").json()
        assert health["ok"] is True
        assert "queue" in health
