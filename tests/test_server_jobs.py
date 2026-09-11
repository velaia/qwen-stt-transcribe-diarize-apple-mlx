"""Job lifecycle: cancel, rerun, delete, and crash recovery on restart."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from qstt.pipeline import Result, Segment
from server import jobs as jobs_mod
from server.config import load_settings
from server.db import DB
from tests.test_server_helpers import (
    FakeEngine,
    build_test_app,
    make_wav,
    upload,
    wait_for_status,
    wait_until,
)


class SlowFakeEngine(FakeEngine):
    """Like FakeEngine, but takes ~0.5s and checks in with progress often,
    so a test has a window to call /cancel while the job is 'processing'."""

    def run(self, path, progress=None):
        report = progress or (lambda *a: None)
        report("decode", None)
        report("vad", None)
        report("language", None)
        if self.opts.diarize:
            report("diarize", None)
        for i in range(10):
            time.sleep(0.05)
            report("transcribe", i / 10)
        report("finalize", None)
        report("done", 1.0)
        return Result(
            str(path), 5.0, "English",
            [Segment(0.0, 5.0, "hello", None, "English", [])],
            [], [], {"vad": "fake", "speech_ratio": 1.0},
        )


def test_cancel_processing_job(tmp_path):
    wav = make_wav(tmp_path / "a.wav", seconds=1.0)
    app = build_test_app(tmp_path / "data", engine_factory=SlowFakeEngine)
    with TestClient(app) as client:
        item_id = upload(client, wav).json()["id"]

        def is_processing():
            return client.get(f"/api/transcripts/{item_id}").json()["status"] == "processing"

        assert wait_until(is_processing, timeout=5.0)
        cancel_resp = client.post(f"/api/transcripts/{item_id}/cancel")
        assert cancel_resp.status_code == 200

        final = wait_for_status(client, item_id)
        assert final.json()["status"] == "cancelled"


def test_cancel_queued_job_never_runs(tmp_path):
    wav1 = make_wav(tmp_path / "a.wav", seconds=1.0)
    wav2 = make_wav(tmp_path / "b.wav", seconds=1.0)
    app = build_test_app(tmp_path / "data", engine_factory=SlowFakeEngine)
    with TestClient(app) as client:
        id1 = upload(client, wav1).json()["id"]
        id2 = upload(client, wav2).json()["id"]

        def id2_queued():
            return client.get(f"/api/transcripts/{id2}").json()["status"] == "queued"

        assert wait_until(id2_queued, timeout=2.0)
        cancel_resp = client.post(f"/api/transcripts/{id2}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        wait_for_status(client, id1)  # let the queue drain
        final2 = client.get(f"/api/transcripts/{id2}").json()
        assert final2["status"] == "cancelled"


def test_rerun_resets_speaker_names_and_swaps_result(tmp_path):
    wav = make_wav(tmp_path / "meeting.wav", seconds=10.0)
    with TestClient(build_test_app(tmp_path / "data")) as client:
        resp = upload(client, wav, options={"diarize": True})
        item_id = resp.json()["id"]
        wait_for_status(client, item_id)

        client.patch(f"/api/transcripts/{item_id}",
                     json={"speaker_names": {"SPEAKER_00": "Alice"}})
        assert client.get(f"/api/transcripts/{item_id}").json()["speaker_names"] == {
            "SPEAKER_00": "Alice"
        }

        rerun1 = client.post(f"/api/transcripts/{item_id}/rerun",
                              json={"options": {"diarize": True}})
        assert rerun1.status_code == 202
        assert rerun1.json()["status"] == "queued"

        conflict = client.post(f"/api/transcripts/{item_id}/rerun",
                                json={"options": {"diarize": True}})
        assert conflict.status_code == 409

        final = wait_for_status(client, item_id)
        detail = final.json()
        assert detail["status"] == "done"
        assert detail["speaker_names"] == {}

        result = client.get(f"/api/transcripts/{item_id}/result").json()
        assert result["segments"]


def test_delete_removes_folder(tmp_path):
    wav = make_wav(tmp_path / "a.wav", seconds=1.0)
    data_dir = tmp_path / "data"
    with TestClient(build_test_app(data_dir)) as client:
        item_id = upload(client, wav).json()["id"]
        wait_for_status(client, item_id)
        item_dir = data_dir / "items" / item_id
        assert item_dir.exists()

        resp = client.delete(f"/api/transcripts/{item_id}")
        assert resp.status_code == 204
        assert not item_dir.exists()
        assert client.get(f"/api/transcripts/{item_id}").status_code == 404


def test_restart_recovery_processing_becomes_queued_then_done(tmp_path):
    data_dir = tmp_path / "data"

    # Simulate a server that died mid-job: write the DB/files directly,
    # without ever starting a JobWorker for this item.
    settings = load_settings(data_dir)
    settings.items_dir.mkdir(parents=True)
    db = DB(settings.db_path)
    item_id = db.new_id()
    item_dir = settings.items_dir / item_id
    item_dir.mkdir()
    make_wav(item_dir / "original.wav", seconds=1.0)
    opts = jobs_mod.default_web_options()
    db.create(item_id=item_id, title="Recover Me", source_filename="original.wav",
              options=opts, smoothing=jobs_mod.smoothing_from_web_options(opts))
    db.update(item_id, status="processing", stage="transcribe", progress=0.5)
    db.close()

    with TestClient(build_test_app(data_dir)) as client:
        final = wait_for_status(client, item_id)
        assert final.status_code == 200
        assert final.json()["status"] == "done"


class CountingEngine(SlowFakeEngine):
    """Counts runs per item (the item id is the original file's folder name)."""

    runs: dict[str, int] = {}

    def run(self, path, progress=None):
        item_id = path.parent.name
        CountingEngine.runs[item_id] = CountingEngine.runs.get(item_id, 0) + 1
        return super().run(path, progress)


def test_cancel_while_queued_then_rerun_runs_exactly_once(tmp_path):
    """Regression: the cancelled entry stays in the queue; rerun adds another.

    Both used to run -- the second one re-transcribing a finished item.
    """
    CountingEngine.runs = {}
    app = build_test_app(tmp_path / "data", engine_factory=CountingEngine)
    with TestClient(app) as client:
        id1 = upload(client, make_wav(tmp_path / "a.wav")).json()["id"]
        id2 = upload(client, make_wav(tmp_path / "b.wav")).json()["id"]
        client.post(f"/api/transcripts/{id2}/cancel")
        assert client.post(f"/api/transcripts/{id2}/rerun",
                           json={"options": {}}).status_code == 202

        assert wait_for_status(client, id2, statuses=("done",)).json()["status"] == "done"
        time.sleep(1.0)  # longer than one SlowFakeEngine run
        assert CountingEngine.runs[id2] == 1
        assert CountingEngine.runs[id1] == 1


def test_worker_survives_an_exception_after_the_model_finished(tmp_path, monkeypatch):
    """Regression: an error writing results used to kill the worker thread."""
    real_write = jobs_mod.results.write_raw_result
    calls = {"n": 0}

    def flaky_write(item_dir, result):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        real_write(item_dir, result)

    monkeypatch.setattr(jobs_mod.results, "write_raw_result", flaky_write)
    with TestClient(build_test_app(tmp_path / "data")) as client:
        first = upload(client, make_wav(tmp_path / "a.wav")).json()["id"]
        failed = wait_for_status(client, first).json()
        assert failed["status"] == "error"
        assert "disk full" in failed["error"]

        second = upload(client, make_wav(tmp_path / "b.wav")).json()["id"]
        assert wait_for_status(client, second).json()["status"] == "done"


class SlowDiarizingEngine(FakeEngine):
    def run(self, path, progress=None):
        time.sleep(0.5)
        return super().run(path, progress)


def test_resmooth_during_a_rerun_is_not_overwritten(tmp_path):
    """Regression: the job used the smoothing it saw when it started."""
    app = build_test_app(tmp_path / "data", engine_factory=SlowDiarizingEngine)
    with TestClient(app) as client:
        item_id = upload(client, make_wav(tmp_path / "m.wav", seconds=10.0),
                         options={"diarize": True}).json()["id"]
        wait_for_status(client, item_id)
        client.post(f"/api/transcripts/{item_id}/rerun", json={"options": {"diarize": True}})
        assert wait_until(lambda: client.get(f"/api/transcripts/{item_id}")
                          .json()["status"] == "processing")

        aggressive = {"max_words": 3, "max_dur": 1.2, "max_gap": 10.0}
        assert client.post(f"/api/transcripts/{item_id}/resmooth",
                           json=aggressive).status_code == 200

        detail = wait_for_status(client, item_id).json()
        assert detail["status"] == "done"
        assert detail["smoothing"] == aggressive
        segments = client.get(f"/api/transcripts/{item_id}/result").json()["segments"]
        assert len(segments) == 2  # default smoothing would leave 3
