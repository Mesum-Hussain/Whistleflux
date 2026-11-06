"""S1-mini clean-up: request format, line handling, fallbacks and engine unpacking."""

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from whistleflux import s1mini


def _cfg(**s1) -> dict:
    return {
        "speech": {"language": ""},
        "format": {"basic_cleanup": False, "enabled": True, "timeout": 5},
        "s1_mini": s1,
    }


class FakeServer:
    """Answers each transcript from a dict (default: upper-case it) and records requests."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.bodies = []

    def chat(self, body, timeout):
        self.bodies.append(body)
        if self.error:
            raise self.error
        transcript = body["messages"][1]["content"].split("\n", 1)[1]
        return self.answers.get(transcript, transcript.upper())


class FormatTest(unittest.TestCase):
    def _run(self, text, server=None, cfg=None):
        server = server or FakeServer()
        with mock.patch.object(s1mini, "is_downloaded", return_value=True), \
                mock.patch.object(s1mini, "get_server", return_value=server):
            return s1mini.format_text(text, cfg or _cfg()), server

    def test_request_matches_model_card(self):
        _, server = self._run("so um send it")
        body = server.bodies[0]
        self.assertEqual(body["messages"][0], {"role": "system", "content": s1mini.SYSTEM_PROMPT})
        self.assertEqual(body["messages"][1]["content"],
                         "[Styling: semi-formal] [Structure: lists] [Context: general]\nso um send it")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["temperature"], 0)
        self.assertIs(body["cache_prompt"], False)

    def test_settings_from_config_and_bad_values_ignored(self):
        _, server = self._run("hi", cfg=_cfg(styling="casual", structure="poem", context="email"))
        self.assertTrue(server.bodies[0]["messages"][1]["content"].startswith(
            "[Styling: casual] [Structure: lists] [Context: email]\n"))

    def test_each_line_cleaned_and_blank_lines_kept(self):
        out, server = self._run("first line\nsecond\n\nnew paragraph")
        self.assertEqual(out, "FIRST LINE\nSECOND\n\nNEW PARAGRAPH")
        self.assertEqual(len(server.bodies), 3)

    def test_filler_only_line_disappears(self):
        out, _ = self._run("hello there\num\nbye now", FakeServer({"um": ""}))
        self.assertEqual(out, "HELLO THERE\nBYE NOW")

    def test_empty_answer_for_real_sentence_keeps_it(self):
        out, _ = self._run("we need to ship it friday", FakeServer({"we need to ship it friday": ""}))
        self.assertEqual(out, "we need to ship it friday")

    def test_runaway_answer_keeps_input(self):
        out, _ = self._run("ok", FakeServer({"ok": "Sure! " * 30}))
        self.assertEqual(out, "ok")

    def test_server_error_returns_input(self):
        out, _ = self._run("some words", FakeServer(error=OSError("connection refused")))
        self.assertEqual(out, "some words")

    def test_other_languages_skipped(self):
        cfg = _cfg()
        cfg["speech"]["language"] = "de"
        out, server = self._run("guten tag", cfg=cfg)
        self.assertEqual(out, "guten tag")
        self.assertEqual(server.bodies, [])

    def test_not_downloaded_returns_input(self):
        with mock.patch.object(s1mini, "is_downloaded", return_value=False), \
                mock.patch.object(s1mini, "get_server") as get:
            self.assertEqual(s1mini.format_text("hello", _cfg()), "hello")
        get.assert_not_called()

    def test_longer_pieces_get_more_time(self):
        server = FakeServer()
        seen = []
        server.chat = lambda body, timeout: seen.append(timeout) or "ok"
        self._run("short", server)
        self._run("x" * 700, server)
        self.assertEqual(seen[0], 5 + 5 * s1mini.SECONDS_PER_CHAR)
        self.assertEqual(seen[1], 5 + 700 * s1mini.SECONDS_PER_CHAR)

    def test_long_line_split_at_sentence_ends(self):
        sentence = "this is one sentence of a long dictation. "
        line = sentence * 120  # about 5,000 characters
        pieces = s1mini._chunks(line.strip())
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= s1mini.MAX_CHUNK_CHARS for p in pieces))
        self.assertTrue(all(p.endswith(".") for p in pieces))
        self.assertEqual(" ".join(pieces), line.strip())


class NumberListsTest(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(s1mini.number_lists(text), expected)

    def test_counted_sentences(self):
        self.check("I have three tasks. First, buy milk. Second, call Mom. Third, email Sam.",
                   "I have three tasks.\n1. Buy milk\n2. Call Mom\n3. Email Sam")

    def test_one_sentence_with_semicolons_and_finally(self):
        self.check("We need three things: first, milk; second, eggs; and finally, bread.",
                   "We need three things:\n1. Milk\n2. Eggs\n3. Bread")

    def test_intro_ending_in_comma_gets_colon(self):
        self.check("For the launch, first update the docs, second tag the release",
                   "For the launch:\n1. Update the docs\n2. Tag the release")

    def test_bullets_that_kept_the_ordinals(self):
        self.check("To do:\n- First, buy milk\n- Second, call Mom\n- Third, email Sam",
                   "To do:\n1. Buy milk\n2. Call Mom\n3. Email Sam")

    def test_one_line_per_item_from_new_line(self):
        self.check("Firstly, buy milk.\nSecondly, call Mom.", "1. Buy milk\n2. Call Mom")

    def test_marker_with_colon_mid_sentence(self):
        # What S1-mini actually wrote for "i have three things to do first buy milk second ..."
        self.check("I have three things to do first: buy milk. Second, call mom. Third, email Sam.",
                   "I have three things to do:\n1. Buy milk\n2. Call mom\n3. Email Sam")

    def test_number_one_and_1st(self):
        self.check("Number one, sleep. Number two, eat.", "1. Sleep\n2. Eat")
        self.check("1st, sleep. 2nd, eat. 3rd, repeat.", "1. Sleep\n2. Eat\n3. Repeat")

    def test_text_after_the_list_kept(self):
        self.check("First, pack. Second, leave. See you soon!", "1. Pack\n2. Leave\nSee you soon!")

    def test_last_item_ends_at_its_sentence(self):
        self.check("First, call Sam.\nSecond, book it. Use the card.",
                   "1. Call Sam\n2. Book it\nUse the card.")

    def test_middle_item_with_two_sentences_keeps_periods(self):
        self.check("First, call Sam. Say hi. Second, book it.", "1. Call Sam. Say hi.\n2. Book it")

    def test_ordinal_inside_an_item_is_not_a_marker(self):
        self.check("First, fix the second bug. Second, ship it.", "1. Fix the second bug\n2. Ship it")

    def test_left_alone(self):
        for text in [
            "At first I hated it, second thoughts came later.",  # "At first" doesn't start a clause
            "First, I want to thank you all.",                    # nothing counted after it
            "He came second and I came first.",
            "First, second and third place get a prize.",         # empty items
            "I'll go first. Then you go.",
            "Finally, we're done.",
            "The first step is easy.",
            "First, a. Third, b.",                                # out of order
        ]:
            self.check(text, text)

    def test_only_with_lists_structure(self):
        server = FakeServer({"first sleep second eat": "First, sleep. Second, eat."})
        with mock.patch.object(s1mini, "is_downloaded", return_value=True), \
                mock.patch.object(s1mini, "get_server", return_value=server):
            self.assertEqual(s1mini.format_text("first sleep second eat", _cfg()), "1. Sleep\n2. Eat")
            self.assertEqual(s1mini.format_text("first sleep second eat", _cfg(structure="prose")),
                             "First, sleep. Second, eat.")


class JoinTest(unittest.TestCase):
    def test_sentences_joined_with_space(self):
        self.assertEqual(s1mini._join(["One.", "Two."]), "One. Two.")

    def test_text_after_a_list_starts_a_new_line(self):
        self.assertEqual(s1mini._join(["Intro.", "We need:\n- A\n- B\n- C", "After."]),
                         "Intro. We need:\n- A\n- B\n- C\nAfter.")

    def test_list_split_across_pieces_stays_one_item_per_line(self):
        self.assertEqual(s1mini._join(["We need:\n- A\n- B", "- C\n- D"]), "We need:\n- A\n- B\n- C\n- D")

    def test_long_dictation_with_list_end_to_end(self):
        filler = "this is a long sentence about the plan. " * 20  # pushes the list into a second piece
        text = filler + "we need to pack sunscreen a first aid kit and chargers"
        def answer(body, timeout):
            t = body["messages"][1]["content"].split("\n", 1)[1]
            if "we need" in t:
                return "Long text.\nWe need to pack:\n- Sunscreen\n- A first aid kit\n- Chargers"
            return "Long text."
        server = FakeServer()
        server.chat = answer
        with mock.patch.object(s1mini, "is_downloaded", return_value=True), \
                mock.patch.object(s1mini, "get_server", return_value=server), \
                mock.patch.object(s1mini, "MAX_CHUNK_CHARS", 800):
            out = s1mini.format_text(text, _cfg())
        self.assertEqual(out, "Long text. Long text.\nWe need to pack:\n- Sunscreen\n- A first aid kit\n- Chargers")


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _add(self, tf, name, data=b"x", link=None):
        info = tarfile.TarInfo(name)
        if link:
            info.type, info.linkname = tarfile.SYMTYPE, link
            tf.addfile(info)
        else:
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    def test_tar_keeps_server_and_libraries(self):
        archive = self.tmp / "llama-ubuntu-x64.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            self._add(tf, "llama-b1/llama-server", b"server")
            self._add(tf, "llama-b1/libllama.so.0.9.0", b"lib")
            self._add(tf, "llama-b1/libllama.so.0", link="libllama.so.0.9.0")
            self._add(tf, "llama-b1/LICENSE")
            self._add(tf, "llama-b1/llama-bench")
            self._add(tf, "llama-b1/../evil.so")
        dest = self.tmp / "out"
        with mock.patch.object(s1mini, "SERVER_FILE", "llama-server"):
            s1mini.extract_engine(archive, dest)
        self.assertEqual(sorted(p.name for p in dest.iterdir()),
                         ["LICENSE", "libllama.so.0", "libllama.so.0.9.0", "llama-server"])
        self.assertEqual((dest / "libllama.so.0").read_bytes(), b"lib")
        self.assertFalse((self.tmp / "evil.so").exists())

    def test_flat_windows_zip(self):
        archive = self.tmp / "llama-win-cpu-x64.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("llama-server.exe", b"server")
            zf.writestr("llama.dll", b"lib")
            zf.writestr("llama-cli.exe", b"other")
        dest = self.tmp / "out"
        with mock.patch.object(s1mini, "SERVER_FILE", "llama-server.exe"):
            s1mini.extract_engine(archive, dest)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["llama-server.exe", "llama.dll"])


class EngineTagTest(unittest.TestCase):
    def _tag(self, plat, machine, libc="glibc"):
        with mock.patch.object(s1mini.sys, "platform", plat), \
                mock.patch.object(s1mini.platform, "machine", return_value=machine), \
                mock.patch.object(s1mini.platform, "libc_ver", return_value=(libc, "2.40")):
            return s1mini._engine_tag()

    def test_every_tag_has_a_pinned_hash(self):
        tags = [self._tag("linux", "x86_64"), self._tag("linux", "aarch64"),
                self._tag("darwin", "arm64"), self._tag("darwin", "x86_64"),
                self._tag("win32", "AMD64"), self._tag("win32", "ARM64")]
        self.assertEqual(sorted(tags), sorted(s1mini.ENGINE_SHA256))

    def test_musl_refused(self):
        with self.assertRaises(RuntimeError):
            self._tag("linux", "x86_64", libc="")


if __name__ == "__main__":
    unittest.main()
