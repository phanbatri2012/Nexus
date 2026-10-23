import unittest

from nexus_engine.services import auto_casting
from nexus_engine.services.voice_config import normalize_casting_profile


def _voice(
    voice_id: str,
    *,
    gender: str = "any",
    age_group: str = "any",
    roles: list[str] | None = None,
    styles: list[str] | None = None,
    auto_cast: bool = True,
    usage_modes: list[str] | None = None,
    voice_pools: list[str] | None = None,
) -> dict:
    profile = {
        "gender": gender,
        "age_group": age_group,
        "languages": ["vi"],
        "roles": roles or ["guest"],
        "styles": styles or [],
        "auto_cast": auto_cast,
    }
    if usage_modes is not None:
        profile["usage_modes"] = usage_modes
    if voice_pools is not None:
        profile["voice_pools"] = voice_pools
    return {
        "id": voice_id,
        "name": voice_id,
        "status": "active",
        "casting_profile": profile,
    }


class TestAutomaticCasting(unittest.TestCase):
    def setUp(self):
        self.voices = [
            _voice("mc-fixed", roles=["mc"], auto_cast=False),
            _voice("female-young", gender="female", age_group="young", styles=["warm"]),
            _voice("male-senior", gender="male", age_group="senior", styles=["serious"]),
            _voice("female-backup", gender="female", age_group="young"),
        ]
        script = "[MC]: Xin chào.\n[LAN]: Em xin chào.\n[ONG_NAM]: Tôi xin chia sẻ."
        self.manifest = auto_casting.build_speaker_manifest(
            script,
            provided_manifest={
                "speakers": [
                    {
                        "speaker_key": "LAN",
                        "name": "Lan",
                        "gender": "female",
                        "age_group": "young",
                        "styles": ["warm"],
                        "confidence": 0.95,
                    },
                    {
                        "speaker_key": "ONG_NAM",
                        "name": "Ông Nam",
                        "gender": "male",
                        "age_group": "senior",
                        "styles": ["serious"],
                        "confidence": 0.9,
                    },
                ]
            },
        )

    def test_profile_cast_keeps_fixed_mc_and_distinct_matching_guests(self):
        assignments = auto_casting.assign_cast(
            self.manifest,
            self.voices,
            mc_voice_id="mc-fixed",
        )
        by_speaker = {item["speaker_key"]: item for item in assignments}
        self.assertEqual(by_speaker["MC"]["voice_id"], "mc-fixed")
        self.assertEqual(by_speaker["LAN"]["voice_id"], "female-young")
        self.assertEqual(by_speaker["ONG_NAM"]["voice_id"], "male-senior")
        self.assertEqual(len({item["voice_id"] for item in assignments}), 3)

    def test_named_speaker_registry_wins_when_profile_still_matches(self):
        lan_manifest = next(item for item in self.manifest if item["speaker_key"] == "LAN")
        assignments = auto_casting.assign_cast(
            self.manifest,
            self.voices,
            mc_voice_id="mc-fixed",
            registry={
                "LAN": {
                    "voice_id": "female-backup",
                    "profile_signature": auto_casting.profile_signature(lan_manifest),
                }
            },
        )
        lan = next(item for item in assignments if item["speaker_key"] == "LAN")
        self.assertEqual(lan["voice_id"], "female-backup")
        self.assertEqual(lan["source"], "registry")

    def test_registry_is_ignored_when_profile_signature_is_stale(self):
        assignments = auto_casting.assign_cast(
            self.manifest,
            self.voices,
            mc_voice_id="mc-fixed",
            registry={
                "LAN": {"voice_id": "female-backup", "profile_signature": "stale"}
            },
            source_video_id="source-1",
            prompt_version="prompt-1",
        )
        lan = next(item for item in assignments if item["speaker_key"] == "LAN")
        self.assertNotEqual(lan["source"], "registry")

    def test_missing_distinct_guest_voice_requires_attention(self):
        with self.assertRaises(auto_casting.AutoCastingAttentionRequired):
            auto_casting.assign_cast(
                self.manifest,
                [self.voices[0], self.voices[1]],
                mc_voice_id="mc-fixed",
            )

    def test_inactive_or_opted_out_voice_is_never_auto_cast(self):
        opted_out = _voice("guest-disabled", auto_cast=False)
        normalized = normalize_casting_profile(opted_out["casting_profile"])
        self.assertFalse(normalized["auto_cast"])
        guest_manifest = [
            {"speaker_key": "MC", "speaker_name": "MC", "role": "mc"},
            {
                "speaker_key": "GUEST",
                "speaker_name": "Guest",
                "role": "guest",
                "gender": "any",
                "age_group": "any",
                "language": "vi",
                "styles": [],
                "confidence": 0.3,
            },
        ]
        with self.assertRaises(auto_casting.AutoCastingAttentionRequired):
            auto_casting.assign_cast(
                guest_manifest,
                [self.voices[0], opted_out],
                mc_voice_id="mc-fixed",
            )

    def test_explicit_usage_modes_override_legacy_auto_cast_flag(self):
        normalized = normalize_casting_profile(
            {
                "auto_cast": True,
                "usage_modes": ["single_narrator"],
                "voice_pools": ["kenh-suc-khoe"],
            }
        )
        self.assertEqual(normalized["usage_modes"], ["single_narrator"])
        self.assertEqual(normalized["voice_pools"], ["kenh-suc-khoe"])
        self.assertFalse(normalized["auto_cast"])

    def test_radar_cast_uses_only_multi_guest_voices_in_selected_pool(self):
        voices = [
            _voice(
                "mc-fixed",
                roles=["mc"],
                auto_cast=False,
                usage_modes=["multi_host"],
            ),
            _voice(
                "single-only",
                gender="female",
                age_group="young",
                usage_modes=["single_narrator"],
                voice_pools=["kenh-suc-khoe"],
            ),
            _voice(
                "wrong-pool",
                gender="female",
                age_group="young",
                usage_modes=["multi_guest"],
                voice_pools=["kenh-tai-chinh"],
            ),
            _voice(
                "allowed-guest",
                gender="female",
                age_group="young",
                usage_modes=["multi_guest"],
                voice_pools=["kenh-suc-khoe"],
            ),
        ]
        assignments = auto_casting.assign_cast(
            self.manifest[:2],
            voices,
            mc_voice_id="mc-fixed",
            guest_voice_pool="kenh-suc-khoe",
            enforce_usage_policy=True,
        )
        self.assertEqual(assignments[1]["voice_id"], "allowed-guest")
        self.assertEqual(assignments[1]["voice_pool"], "kenh-suc-khoe")

    def test_stored_cast_from_another_pool_is_rejected(self):
        voices = [
            _voice(
                "mc-fixed",
                roles=["mc"],
                auto_cast=False,
                usage_modes=["multi_host"],
            ),
            _voice(
                "guest-finance",
                usage_modes=["multi_guest"],
                voice_pools=["kenh-tai-chinh"],
            ),
        ]
        with self.assertRaises(auto_casting.AutoCastingAttentionRequired):
            auto_casting.validate_cast_policy(
                [
                    {"role_key": "MC", "voice_id": "mc-fixed"},
                    {"role_key": "GUEST", "voice_id": "guest-finance"},
                ],
                voices,
                mc_voice_id="mc-fixed",
                guest_voice_pool="kenh-suc-khoe",
            )

    def test_role_aliases_share_one_canonical_key(self):
        self.assertEqual(auto_casting.normalize_speaker_key("guest_1"), "KHACH_1")
        self.assertEqual(auto_casting.normalize_speaker_key("GUEST1"), "KHACH_1")
        self.assertEqual(auto_casting.normalize_speaker_key("KHACH_1"), "KHACH_1")

    def test_manifest_schema_rejects_unknown_role_and_invalid_enum(self):
        script = "[MC]: Chào.\n[KHACH_1]: Xin chào."
        invalid_role = {
            "speakers": [
                {"speaker_key": "MC", "name": "MC", "gender": "male", "age_group": "adult", "language": "vi", "styles": [], "confidence": 1},
                {"speaker_key": "KHACH_2", "name": "Khách", "gender": "female", "age_group": "adult", "language": "vi", "styles": [], "confidence": .9},
            ]
        }
        with self.assertRaises(auto_casting.CastingManifestValidationError):
            auto_casting.validate_casting_manifest(script, invalid_role)
        invalid_enum = {
            "speakers": [
                {"speaker_key": "MC", "name": "MC", "gender": "unknown", "age_group": "adult", "language": "vi", "styles": [], "confidence": 1},
                {"speaker_key": "KHACH_1", "name": "Khách", "gender": "female", "age_group": "adult", "language": "vi", "styles": [], "confidence": .9},
            ]
        }
        with self.assertRaises(auto_casting.CastingManifestValidationError):
            auto_casting.validate_casting_manifest(script, invalid_enum)

    def test_character_profile_beats_opposite_source_timbre(self):
        assignments = auto_casting.assign_cast(
            self.manifest[:2],
            self.voices,
            mc_voice_id="mc-fixed",
            source_voice_profile={"gender": "male", "confidence": .99},
        )
        self.assertEqual(assignments[1]["voice_id"], "female-young")
        self.assertEqual(assignments[1]["source"], "manifest_profile")

    def test_one_generic_role_uses_high_confidence_source_timbre(self):
        manifest = auto_casting.build_speaker_manifest(
            "[MC]: Chào.\n[GUEST1]: Xin chào."
        )
        assignments = auto_casting.assign_cast(
            manifest,
            self.voices,
            mc_voice_id="mc-fixed",
            source_voice_profile={"gender": "male", "confidence": .91},
            source_video_id="youtube-source",
            prompt_version="prompt-v2",
        )
        guest = assignments[1]
        self.assertEqual(guest["voice_id"], "male-senior")
        self.assertEqual(guest["source"], "source_voice_fallback")

    def test_generic_role_with_mixed_pool_requires_attention(self):
        manifest = auto_casting.build_speaker_manifest(
            "[MC]: Chào.\n[KHACH_1]: Xin chào."
        )
        with self.assertRaises(auto_casting.AutoCastingAttentionRequired):
            auto_casting.assign_cast(
                manifest,
                self.voices,
                mc_voice_id="mc-fixed",
                source_voice_profile={"gender": "any", "confidence": .4},
            )

    def test_unknown_requirement_does_not_reward_gender_or_age_metadata(self):
        manifest = auto_casting.build_speaker_manifest(
            "[MC]: Chào.\n[KHACH_1]: Xin chào."
        )[1]
        homogeneous = [
            _voice("female-specific", gender="female", age_group="young"),
            _voice("female-any-age", gender="female", age_group="any"),
        ]
        candidates = auto_casting.list_candidates(
            manifest,
            homogeneous,
            guest_voice_pool="",
        )
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(candidate["breakdown"]["gender"], 0)
            self.assertEqual(candidate["breakdown"]["age_group"], 0)

    def test_known_profile_rejects_voice_with_any_metadata(self):
        voices = [self.voices[0], _voice("unknown", gender="any", age_group="any")]
        with self.assertRaises(auto_casting.AutoCastingAttentionRequired):
            auto_casting.assign_cast(
                self.manifest[:2],
                voices,
                mc_voice_id="mc-fixed",
            )

    def test_rendezvous_selection_is_stable_and_not_lexical_uuid_order(self):
        manifest = auto_casting.build_speaker_manifest(
            "[MC]: Chào.\n[LAN]: Xin chào.",
            provided_manifest={
                "speakers": [
                    {"speaker_key": "LAN", "name": "Lan", "gender": "female", "age_group": "young", "language": "vi", "styles": [], "confidence": .9}
                ]
            },
        )
        voices = [
            self.voices[0],
            _voice("0000-a", gender="female", age_group="young"),
            _voice("zzzz-z", gender="female", age_group="young"),
        ]
        first = auto_casting.assign_cast(
            manifest,
            voices,
            mc_voice_id="mc-fixed",
            source_video_id="video-77",
            prompt_version="prompt-9",
        )
        second = auto_casting.assign_cast(
            manifest,
            list(reversed(voices)),
            mc_voice_id="mc-fixed",
            source_video_id="video-77",
            prompt_version="prompt-9",
        )
        self.assertEqual(first[1]["voice_id"], second[1]["voice_id"])


if __name__ == "__main__":
    unittest.main()
