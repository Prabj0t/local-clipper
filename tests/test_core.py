import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from clipper.captions import ass, clip_cues, srt
from clipper.composition import filter_graph
from clipper.config import Config
from clipper.domain import (
    Clip, Cue, JobRequest, Layout, ProjectCreate, ProjectEdit, Region, Settings,
    Status, Word, canonical_source,
)
from clipper.media import render_key, source_identity
from clipper.pipeline import Pipeline
from clipper.processes import Blocked
from clipper.selection import select_clips
from clipper.storage import Conflict, InstanceLock, Store, atomic_json, fingerprint
from clipper.tracking import smooth_centers


class ContractsTests(unittest.TestCase):
    def test_regions_and_times_reject_invalid_geometry(self):
        for value in ({"x": 0.9, "width": 0.2}, {"height": 0}, {"x": float("nan")}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Region(**value)
        with self.assertRaises(ValidationError):
            Clip(id="c1", start=20, end=10)
        with self.assertRaises(ValidationError):
            Clip(id="../escape", start=0, end=10)
        with self.assertRaises(ValidationError):
            Settings(min_duration=50, max_duration=10)
        with self.assertRaises(ValidationError):
            Settings(min_score=float("inf"))

    def test_single_youtube_video_is_canonicalized_without_fetching(self):
        self.assertEqual(
            canonical_source("https://youtu.be/abcdefghijk?t=42"),
            ("youtube", "https://www.youtube.com/watch?v=abcdefghijk"),
        )
        for url in (
            "https://example.com/watch?v=abcdefghijk",
            "https://www.youtube.com.evil.example/watch?v=abcdefghijk",
            "https://user:password@youtube.com/watch?v=abcdefghijk",
            "https://youtube.com/watch?v=abcdefghijk&list=playlist",
            "https://youtu.be/abcdefghijk?list=playlist",
            "http://youtube.com/watch?v=abcdefghijk",
            "https://youtube.com:8443/watch?v=abcdefghijk",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonical_source(url)

    def test_unknown_settings_do_not_silently_change_behavior(self):
        with self.assertRaises(ValidationError):
            Settings(aspect_ratio="4:3")
        with self.assertRaises(ValidationError):
            Layout(width=800)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source with spaces.mp4"
        self.source.write_bytes(b"unit test source")
        self.store = Store(self.root / "data")
        self.store.initialize()
        self.project = self.store.create(ProjectCreate(name="Test", source=str(self.source)))

    def tearDown(self):
        self.temporary.cleanup()

    def test_edit_uses_optimistic_revision_and_rejects_active_jobs(self):
        edit = ProjectEdit(revision=0, settings=Settings())
        saved = self.store.edit(self.project["id"], edit)
        self.assertEqual(saved["revision"], 1)
        with self.assertRaises(Conflict):
            self.store.edit(self.project["id"], edit)
        self.store.enqueue(self.project["id"], JobRequest(action="inspect"))
        with self.assertRaises(Conflict):
            self.store.edit(self.project["id"], ProjectEdit(revision=1, settings=Settings()))

    def test_one_active_job_per_project_and_cancellation_does_not_run_it(self):
        job = self.store.enqueue(self.project["id"], JobRequest(action="inspect"))
        with self.assertRaises(Conflict):
            self.store.enqueue(self.project["id"], JobRequest(action="render"))
        cancelled = self.store.cancel(job["id"])
        self.assertEqual(cancelled["status"], Status.CANCELLED)
        self.assertIsNone(self.store.claim())

    def test_removal_hides_project_but_preserves_files_and_finished_jobs(self):
        project_id = self.project["id"]
        directory = self.store.project_dir(project_id)
        files = {
            self.source: b"unit test source",
            directory / "source" / "source.mp4": b"downloaded video",
            directory / "previews" / "clip.mp4": b"preview",
            directory / "exports" / "clip.mp4": b"export",
            directory / "exports" / "clip.srt": b"subtitles",
            directory / "cache" / "evidence.json": b"{}",
            directory / "logs" / "job.log": b"job log",
            self.root / "models" / "shared.bin": b"shared model fixture",
        }
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        other = self.store.create(ProjectCreate(name="Keep", source=str(self.source)))
        job = self.store.enqueue(project_id, JobRequest(action="inspect"))
        self.store.claim()
        self.store.finish(job["id"], Status.SUCCEEDED)

        self.store.remove(project_id, revision=0)
        self.store.initialize()

        self.assertEqual([item["id"] for item in self.store.projects()], [other["id"]])
        with self.assertRaises(KeyError):
            self.store.project(project_id)
        with self.assertRaises(KeyError):
            self.store.edit(project_id, ProjectEdit(revision=0, settings=Settings()))
        with self.assertRaises(KeyError):
            self.store.enqueue(project_id, JobRequest(action="analyze"))
        self.assertEqual(self.store.job(job["id"])["status"], Status.SUCCEEDED)
        for path, content in files.items():
            self.assertEqual(path.read_bytes(), content)

    def test_removal_rejects_queued_running_and_cancelling_jobs(self):
        project_id = self.project["id"]
        job = self.store.enqueue(project_id, JobRequest(action="inspect"))
        with self.assertRaisesRegex(Conflict, "active job"):
            self.store.remove(project_id, revision=0)
        self.store.claim()
        with self.assertRaisesRegex(Conflict, "active job"):
            self.store.remove(project_id, revision=0)
        self.store.cancel(job["id"])
        with self.assertRaisesRegex(Conflict, "active job"):
            self.store.remove(project_id, revision=0)
        self.store.finish(job["id"], Status.CANCELLED)
        self.store.remove(project_id, revision=0)
        self.assertEqual(self.store.projects(), [])

    def test_removal_rejects_stale_revision(self):
        project_id = self.project["id"]
        self.store.publish(project_id, self.project["document"])
        with self.assertRaisesRegex(Conflict, "Reload"):
            self.store.remove(project_id, revision=0)
        self.assertEqual(self.store.project(project_id)["revision"], 1)
        self.store.remove(project_id, revision=1)
        self.assertEqual(self.store.projects(), [])

    def test_original_database_is_migrated_without_losing_projects(self):
        legacy = Store(self.root / "legacy")
        legacy.root.mkdir()
        with legacy.connection() as db:
            db.executescript("""
                CREATE TABLE projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    source TEXT NOT NULL, settings TEXT NOT NULL, document TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)
            db.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.project["id"], self.project["name"], self.project["kind"],
                    self.project["source"], json.dumps(self.project["settings"]),
                    json.dumps(self.project["document"]), self.project["revision"],
                    self.project["created_at"], self.project["updated_at"],
                ),
            )
        legacy.initialize()
        migrated = legacy.project(self.project["id"])
        self.assertEqual(migrated["source"], self.project["source"])
        self.assertEqual(migrated["document"], self.project["document"])
        legacy.remove(migrated["id"], migrated["revision"])
        legacy.initialize()
        self.assertEqual(legacy.projects(), [])
        self.assertEqual(self.source.read_bytes(), b"unit test source")

    def test_restart_interrupts_running_work_but_keeps_queued_work(self):
        job = self.store.enqueue(self.project["id"], JobRequest(action="inspect"))
        self.assertEqual(self.store.claim()["id"], job["id"])
        self.store.recover()
        self.assertEqual(self.store.job(job["id"])["status"], Status.INTERRUPTED)
        retry = self.store.enqueue(self.project["id"], JobRequest(action="inspect"))
        self.store.recover()
        self.assertEqual(self.store.job(retry["id"])["status"], Status.QUEUED)

    def test_cancel_race_cannot_be_reported_as_success(self):
        job = self.store.enqueue(self.project["id"], JobRequest(action="inspect"))
        self.store.claim()
        self.store.cancel(job["id"])
        self.store.finish(job["id"], Status.SUCCEEDED)
        self.assertEqual(self.store.job(job["id"])["status"], Status.CANCELLED)

    def test_source_bounds_and_duplicate_clip_ids_are_rejected(self):
        document = self.project["document"]
        document["media"] = {"duration": 30}
        self.store.publish(self.project["id"], document)
        with self.assertRaises(ValueError):
            self.store.edit(
                self.project["id"],
                ProjectEdit(revision=1, settings=Settings(), clips=[Clip(id="one", start=25, end=40)]),
            )
        with self.assertRaises(ValidationError):
            ProjectEdit(revision=1, settings=Settings(), clips=[
                Clip(id="same", start=0, end=10), Clip(id="same", start=10, end=20),
            ])

    def test_instance_lock_prevents_two_coordinators(self):
        first, second = InstanceLock(self.root), InstanceLock(self.root)
        first.acquire()
        try:
            with self.assertRaises(RuntimeError):
                second.acquire()
        finally:
            first.release()
        second.acquire()
        second.release()

    def test_atomic_artifact_replaces_only_after_complete_serialization(self):
        path = self.root / "artifact.json"
        atomic_json(path, {"complete": True})
        with self.assertRaises(TypeError):
            atomic_json(path, {"unsupported": object()})
        self.assertEqual(json.loads(path.read_text()), {"complete": True})
        self.assertEqual(list(self.root.glob(".artifact.json.*")), [])
        self.assertNotEqual(fingerprint({"layout": 1}), fingerprint({"layout": 2}))

    def test_missing_ffmpeg_blocks_instead_of_installing(self):
        job = self.store.enqueue(self.project["id"], JobRequest(action="inspect"))
        self.store.claim()
        config = Config(data_dir=self.store.root)
        with patch.object(Config, "tool", return_value=None), self.assertRaises(Blocked):
            Pipeline(config, self.store, job).run()
        self.assertEqual(self.source.read_bytes(), b"unit test source")

    def test_youtube_acquisition_requires_permission_before_importing_runtime(self):
        from clipper.sources import youtube
        with self.assertRaisesRegex(Blocked, "Explicit approval"):
            youtube(
                "https://www.youtube.com/watch?v=abcdefghijk", self.root,
                Settings(), Config(), download=True, approved=False, check_cancel=lambda: None,
            )
        self.assertFalse((self.root / "source.mp4").exists())

    def test_analysis_preserves_inspected_replay_when_download_metadata_omits_it(self):
        inspected = [{"start_time": 10, "end_time": 20, "value": 0.8}]
        downloaded = [{"start_time": 30, "end_time": 40, "value": 0.9}]
        for incoming, expected in (([], inspected), (None, inspected), (downloaded, downloaded)):
            with self.subTest(download_replay=incoming):
                project = self.store.create(ProjectCreate(
                    name="Replay", source="https://youtu.be/abcdefghijk",
                    settings=Settings(captions=False),
                ))
                document = project["document"]
                document["remote"] = {"replay": inspected}
                document["download"] = {"local_path": str(self.source), "replay": incoming}
                self.store.publish(project["id"], document)
                job = self.store.enqueue(project["id"], JobRequest(action="analyze"))
                self.store.claim()
                media = {
                    "identity": source_identity(self.source), "duration": 60, "audio": False,
                }
                with (
                    patch.object(Config, "tool", return_value="ffmpeg"),
                    patch("clipper.pipeline.visual_activity", return_value=[0] * 60),
                    patch("clipper.pipeline.select_clips", return_value=[
                        Clip(id="test", start=10, end=20),
                    ]) as select,
                ):
                    Pipeline(
                        Config(data_dir=self.store.root), self.store, job,
                    ).analyze(self.source, media)
                self.store.finish(job["id"], Status.SUCCEEDED)
                self.assertEqual(select.call_args.args[4], expected)
                self.assertNotIn(
                    "Highlights are estimates, not measured audience retention.",
                    self.store.project(project["id"])["document"]["warnings"],
                )


class CaptionSelectionTests(unittest.TestCase):
    def test_clip_caption_timing_is_relative_and_clamped(self):
        clip = Clip(id="test", start=10, end=20)
        result = clip_cues(
            [Cue(start=8, end=12, text="Opening"), Cue(start=19, end=23, text="Ending")], clip,
        )
        self.assertEqual([(cue.start, cue.end) for cue in result], [(0, 2), (9, 10)])
        self.assertIn("00:00:00,000 --> 00:00:02,000", srt(result))

    def test_caption_text_cannot_inject_ass_overrides(self):
        output = ass([Cue(start=0, end=2, text=r"{\pos(0,0)} hello \N world")], 720, 1280)
        dialogue = next(line for line in output.splitlines() if line.startswith("Dialogue:"))
        self.assertNotIn(r"\pos(0,0)", dialogue)
        self.assertEqual(dialogue.count(r"\pos("), 1)

    def test_phrase_and_word_captions_are_centered_below_midpoint_at_each_resolution(self):
        cues = [
            Cue(start=0, end=2, text="Two words", words=[
                Word(start=0, end=1, text="Two"), Word(start=1, end=2, text="words"),
            ]),
            Cue(start=3, end=5, text="A phrase\non two lines"),
        ]
        for width, height in ((360, 640), (720, 1280), (1080, 1920)):
            with self.subTest(width=width):
                output = ass(cues, width, height)
                style = next(line for line in output.splitlines() if line.startswith("Style:"))
                self.assertEqual(style.split(",")[18], "5")
                lines = [line for line in output.splitlines() if line.startswith("Dialogue:")]
                self.assertEqual(len(lines), 3)
                anchor = rf"{{\pos({round(width * .5)},{round(height * .6)})}}"
                for line in lines:
                    self.assertTrue(line.split(",", 9)[9].startswith(anchor))
                self.assertIn(r"{\c&H00E3F7A7&}", lines[0])
                self.assertIn(r"A phrase\Non two lines", lines[-1])

    def test_caption_placement_change_invalidates_only_captioned_render_cache(self):
        clip = Clip(id="one", start=0, end=10).model_dump()
        for cues in ([], [Cue(start=0, end=2, text="Caption").model_dump()]):
            for preview in (False, True):
                with self.subTest(captioned=bool(cues), preview=preview):
                    previous = fingerprint({
                        "version": 1, "source": {}, "clip": {"start": 0.0, "end": 10.0},
                        "layout": {}, "cues": cues, "preview": preview,
                    })
                    current = render_key({}, clip, {}, cues, preview)
                    if cues:
                        self.assertNotEqual(current, previous)
                    else:
                        self.assertEqual(current, previous)

    def test_silence_and_constant_sound_do_not_pad_clip_counts(self):
        self.assertEqual(select_clips([], [0] * 100, 100, Settings(max_clips=10)), [])
        self.assertEqual(select_clips([], [0.8] * 100, 100, Settings(max_clips=10)), [])

    def test_strong_candidates_are_deduplicated_and_capped(self):
        energy = [0.05] * 240
        for index in (30, 31, 32, 130, 131, 132, 210, 211, 212):
            energy[index] = 0.9
        result = select_clips(
            [], energy, 240, Settings(min_duration=10, max_duration=20, max_clips=2, min_score=0.1),
        )
        self.assertEqual(len(result), 2)
        self.assertLessEqual(result[0].end, result[1].start)
        self.assertTrue(all(0 <= clip.start < clip.end <= 240 for clip in result))

    def test_short_source_is_not_forced_into_multiple_clips(self):
        cue = Cue(start=0, end=7, text="This is a complete short sentence that can stand on its own.")
        clips = select_clips([cue], [], 7, Settings())
        self.assertEqual(len(clips), 1)
        self.assertEqual((clips[0].start, clips[0].end), (0, 7))

    def test_gaming_regions_share_one_timeline_and_only_portrait_dimensions(self):
        layout = Layout(mode="gaming", facecam=Region(x=0.75, y=0, width=0.25, height=0.25))
        graph, width, height = filter_graph(layout, 1920, 1080, preview=False, captions=True)
        self.assertEqual((width, height), (720, 1280))
        self.assertIn("split=2", graph)
        self.assertIn("crop=480:270:1440:0", graph)
        self.assertIn("pad=", graph)
        self.assertIn("vstack=inputs=2", graph)
        self.assertNotIn("[0:a]", graph)
        with self.assertRaises(Blocked):
            filter_graph(Layout(mode="gaming"), 1920, 1080, preview=False, captions=False)

    def test_tracking_holds_misses_limits_motion_and_resets_at_cuts(self):
        result = smooth_centers(
            [(0, 0.51, False), (1, 0.9, False), (2, None, False), (3, 0.1, True)], 0.5,
        )
        self.assertEqual(result[0][1], 0.5)
        self.assertLessEqual(result[1][1] - result[0][1], 0.12 + 1e-8)
        self.assertEqual(result[2][1], result[1][1])
        self.assertEqual(result[3][1], 0.1)

    def test_clip_title_edits_do_not_invalidate_encoded_video(self):
        first = Clip(id="one", start=0, end=10, title="Before").model_dump()
        second = {**first, "title": "After", "score": 0.9}
        self.assertEqual(
            render_key({}, first, {}, [], False),
            render_key({}, second, {}, [], False),
        )

    def test_visual_event_without_speech_remains_eligible(self):
        motion = [0.0] * 60
        motion[25] = 0.5
        clips = select_clips([], [], 60, Settings(), motion=motion)
        self.assertEqual(len(clips), 1)
        self.assertLess(clips[0].start, 25)
        self.assertGreater(clips[0].end, 25)


if __name__ == "__main__":
    unittest.main()
