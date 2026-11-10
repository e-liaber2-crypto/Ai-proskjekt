"""Valider kreative klippeplanar lokalt, utan Google-kall eller API-nøklar."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app import media
from app.gemini import edit_instruction, editorial_requirements
from app.cut_plan import parse_plan


class EditorialRequirementsTests(unittest.TestCase):
    def test_users_ranking_request_preserves_title_order_and_narration(self):
        description = (
            "Lag ein rangeringsvideo der du rangerer frå 1 til 3, asså best til verst der du har tal "
            "som viser oppe til venstre gjennom heile videoen. Talene skal vere fargerike, Du begynner "
            "med å settje opp ein tekst der det står rangering, og under der så kjem talene opp etter "
            "kvar video som viser. Gjerne snakk igjennom videon der du fortelljer kor fint det er på klippene"
        )
        brief = editorial_requirements(description)
        self.assertEqual(brief, {
            "ranking_requested": True, "ranking_direction": "best_first",
            "narration_requested": True, "title": "Rangering",
        })
        instruction = edit_instruction(description, 30, "professional", 3)
        self.assertEqual(instruction["requirements"], brief)
        self.assertEqual(instruction["video_type"], "ranking")
        self.assertEqual(instruction["instruction"], description)

    def test_best_first_and_worst_first_requests_keep_their_direction(self):
        for description, expected in (
            ("Ranger frå best til verst.", "best_first"),
            ("Ranger frå dårlegast til best.", "worst_first"),
            ("Rank these clips from worst to best.", "worst_first"),
            ("Ranger frå 1 til 3.", "best_first"),
            ("Ranger frå 3 til 1.", "worst_first"),
        ):
            with self.subTest(description=description):
                brief = editorial_requirements(description)
                self.assertTrue(brief["ranking_requested"])
                self.assertEqual(brief["ranking_direction"], expected)

    def test_explicit_narration_negation_does_not_activate_speech(self):
        for description in (
            "Lag ei rangering utan forteljarstemme.",
            "Lag ei rangering, men ikkje snakk over videoen.",
            "Rank these clips without narration.",
            "Rank these clips. Do not add voiceover.",
            "Lag ei rangering. Ikkje legg til forteljarstemme.",
        ):
            with self.subTest(description=description):
                brief = editorial_requirements(description)
                self.assertFalse(brief["narration_requested"])
                instruction = edit_instruction(description, 15, "professional", 3)
                self.assertFalse(instruction["requirements"]["narration_requested"])

    def test_quoted_title_is_preserved_exactly(self):
        brief = editorial_requirements('Rank these clips, with title "My Top Three".')
        self.assertEqual(brief["title"], "My Top Three")


class CreativePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clips = []
        for identifier in ("a", "b", "c"):
            path = Path(self.temp.name) / f"{identifier}.mp4"
            path.write_bytes(b"Local plan fixture; never uploaded")
            self.clips.append({"id": identifier, "path": str(path), "duration": 5.0, "has_audio": False})

    def ranked_response(self, *, direction="best_first"):
        ranks = [1, 2, 3] if direction == "best_first" else [3, 2, 1]
        return {
            "presentation": {"title": "Rangering", "ranking_direction": direction, "narration": False},
            "segments": [
                {"clip_id": clip_id, "start_seconds": 0, "end_seconds": 1, "order": index + 1,
                 "ranking": {"rank": rank, "title": f"Landskap {clip_id}",
                             "reason": "Lys og landskapsdjupn gjer motivet tydeleg.", "criterion": "Visuelt inntrykk"}}
                for index, (clip_id, rank) in enumerate(zip(("c", "a", "b"), ranks))
            ],
        }

    def parse(self, response, **kwargs):
        return parse_plan(json.dumps(response), self.clips, 3, **kwargs)

    def test_best_first_ranking_preserves_quality_order_and_one_presentation(self):
        plan = self.parse(self.ranked_response(), require_ranking=True, ranking_direction="best_first")
        self.assertEqual([part["ranking"]["rank"] for part in plan], [1, 2, 3])
        self.assertEqual([part["clip_id"] for part in plan], ["c", "a", "b"])
        self.assertEqual(plan[0]["presentation"]["title"], "Rangering")
        self.assertEqual(plan[0]["presentation"]["ranking_direction"], "best_first")
        self.assertFalse(any("presentation" in part for part in plan[1:]))

    def test_explicit_worst_first_ranking_is_supported(self):
        plan = self.parse(self.ranked_response(direction="worst_first"), require_ranking=True,
                          ranking_direction="worst_first")
        self.assertEqual([part["ranking"]["rank"] for part in plan], [3, 2, 1])

    def test_model_cannot_reverse_requested_ranking(self):
        with self.assertRaises(media.MediaError):
            self.parse(self.ranked_response(direction="worst_first"), require_ranking=True,
                       ranking_direction="best_first")
        response = self.ranked_response()
        response["segments"].reverse()
        for index, part in enumerate(response["segments"]):
            part["order"] = index + 1
        with self.assertRaises(media.MediaError):
            self.parse(response, require_ranking=True, ranking_direction="best_first")

    def test_omitted_presentation_gets_requested_direction_and_ranking_title(self):
        response = self.ranked_response(direction="worst_first")
        del response["presentation"]
        plan = self.parse(response, require_ranking=True, ranking_direction="worst_first")
        self.assertEqual(plan[0]["presentation"], {
            "title": "Rangering", "ranking_direction": "worst_first", "narration": False,
        })

    def test_explicit_title_is_preserved_over_model_suggestion(self):
        response = self.ranked_response()
        response["presentation"]["title"] = "Ein annan tittel"
        plan = self.parse(response, expected_title="Rangering")
        self.assertEqual(plan[0]["presentation"]["title"], "Rangering")

    def test_rankings_require_every_clip_once_and_consistent_criterion(self):
        variants = []
        duplicate_rank = self.ranked_response()
        duplicate_rank["segments"][1]["ranking"]["rank"] = 1
        variants.append(duplicate_rank)
        duplicate_clip = self.ranked_response()
        duplicate_clip["segments"][1]["clip_id"] = "c"
        variants.append(duplicate_clip)
        missing_label = self.ranked_response()
        missing_label["segments"][1]["ranking"] = None
        variants.append(missing_label)
        different_criterion = self.ranked_response()
        different_criterion["segments"][1]["ranking"]["criterion"] = "Lydkvalitet"
        variants.append(different_criterion)
        for response in variants:
            with self.subTest(response=variants.index(response)), self.assertRaises(media.MediaError):
                self.parse(response, require_ranking=True)

    def test_requested_narration_requires_script_for_every_clip(self):
        response = self.ranked_response()
        for part in response["segments"]:
            part["voiceover"] = "Sjå kor fint lyset fell over fjella."
        plan = self.parse(response, narration_requested=True)
        self.assertTrue(plan[0]["presentation"]["narration"])
        self.assertTrue(all(part["voiceover"] for part in plan))
        del response["segments"][1]["voiceover"]
        with self.assertRaises(media.MediaError):
            self.parse(response, narration_requested=True)

    def test_model_cannot_enable_unrequested_narration(self):
        response = self.ranked_response()
        response["presentation"]["narration"] = True
        plan = self.parse(response)
        self.assertFalse(plan[0]["presentation"]["narration"])

    def test_nonranking_plans_support_optional_title_and_narration(self):
        response = self.ranked_response()
        del response["presentation"]
        for part in response["segments"]:
            del part["ranking"]
        plain_plan = self.parse(response)
        self.assertTrue(all("presentation" not in part and "voiceover" not in part for part in plain_plan))
        for part in response["segments"]:
            part["voiceover"] = "Dette landskapet har vakre fargar."
        plan = self.parse(response, narration_requested=True, expected_title="Vakre landskap")
        self.assertEqual(plan[0]["presentation"]["title"], "Vakre landskap")
        self.assertTrue(plan[0]["presentation"]["narration"])
        self.assertTrue(all("ranking" not in part for part in plan))

    def test_creative_metadata_is_bounded_and_single_line(self):
        invalid_fields = [
            ("presentation", "title", "x" * 61),
            ("presentation", "title", "   "),
            ("presentation", "title", "Tittel\nNy linje"),
            ("presentation", "ranking_direction", "random"),
            ("presentation", "narration", "true"),
            ("presentation", "output_path", "outside.mp4"),
            ("segment", "voiceover", "x" * 221),
            ("segment", "voiceover", "   "),
            ("segment", "voiceover", "Privat\x00tekst"),
            ("ranking", "rank", True),
            ("ranking", "reason", "x" * 181),
            ("ranking", "title", "x" * 37),
            ("ranking", "criterion", "x" * 61),
        ]
        for scope, field, value in invalid_fields:
            with self.subTest(scope=scope, field=field):
                response = copy.deepcopy(self.ranked_response())
                target = (response["presentation"] if scope == "presentation" else
                          response["segments"][0] if scope == "segment" else response["segments"][0]["ranking"])
                target[field] = value
                with self.assertRaises(media.MediaError):
                    self.parse(response)


if __name__ == "__main__":
    unittest.main()
