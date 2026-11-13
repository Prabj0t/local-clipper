import subprocess
import tempfile
import unittest
from pathlib import Path

from clipper.config import Config
from clipper.domain import Clip, Cue, JobRequest, Layout, ProjectCreate, ProjectEdit, Region, Settings
from clipper.media import probe
from clipper.pipeline import Pipeline
from clipper.storage import Store

CONFIG = Config()
AVAILABLE = bool(CONFIG.tool("ffmpeg") and CONFIG.tool("ffprobe"))


@unittest.skipUnless(AVAILABLE, "An approved FFmpeg/FFprobe installation is required; never downloaded.")
class MediaTests(unittest.TestCase):
    def test_actual_gaming_render_keeps_both_regions_audio_and_captions(self):
        with tempfile.TemporaryDirectory(prefix="clipper media ") as temporary:
            root = Path(temporary)
            source = root / "synthetic source.mp4"
            subprocess.run(
                [
                    CONFIG.tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i",
                    "color=blue:s=640x360:r=30:d=3,drawbox=x=480:y=0:w=160:h=90:color=red:t=fill",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
                ],
                check=True, capture_output=True, timeout=30,
            )
            config = Config(data_dir=root / "data")
            store = Store(config.data_dir)
            store.initialize()
            settings = Settings(
                layout=Layout(
                    mode="gaming", facecam=Region(x=0.75, y=0, width=0.25, height=0.25),
                ),
            )
            project = store.create(ProjectCreate(name="Synthetic", source=str(source), settings=settings))
            store.edit(
                project["id"],
                ProjectEdit(
                    revision=0, settings=settings,
                    clips=[Clip(id="scene", start=0.5, end=2.5)],
                    cues=[Cue(start=0.5, end=2.5, text="Local caption fixture")],
                ),
            )
            job = store.enqueue(project["id"], JobRequest(action="render"))
            store.claim()
            Pipeline(config, store, job).run()
            result = store.project(project["id"])
            output = store.project_dir(project["id"]) / result["document"]["exports"][0]["video"]
            media = probe(output, config)
            self.assertEqual((media["width"], media["height"]), (720, 1280))
            self.assertTrue(media["audio"])
            self.assertAlmostEqual(media["duration"], 2, delta=0.3)
            pixels = subprocess.run(
                [
                    config.tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error",
                    "-i", str(output), "-frames:v", "1", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "pipe:1",
                ],
                capture_output=True, check=True, timeout=30,
            ).stdout
            for x, y, channel in ((360, 100, 0), (100, 800, 2)):
                offset = (y * 720 + x) * 3
                rgb = pixels[offset:offset + 3]
                self.assertGreater(rgb[channel], 180)
                self.assertLess(rgb[2 if channel == 0 else 0], 80)
            self.assertTrue(source.is_file())
            self.assertTrue(output.with_suffix(".srt").is_file())


if __name__ == "__main__":
    unittest.main()
