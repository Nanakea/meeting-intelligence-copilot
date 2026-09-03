from app.evals.pilot_matrix import routed_audio_matrix
from app.evals.routed_audio_assets import build_routed_audio_asset_plan


def test_asset_plan_covers_every_routed_audio_scenario_once() -> None:
    plan = build_routed_audio_asset_plan()

    assert len(plan) == 108
    assert {item.scenario_id for item in plan} == {
        scenario.scenario_id for scenario in routed_audio_matrix()
    }
    assert all(item.fixture.is_file() and item.golden.is_file() for item in plan)
    assert all(item.utterances for item in plan)


def test_asset_plan_keeps_both_voices_and_all_profiles_for_every_group() -> None:
    plan = build_routed_audio_asset_plan()
    groups = {(item.category, item.lang) for item in plan}

    for group in groups:
        items = [item for item in plan if (item.category, item.lang) == group]
        assert {item.seed for item in items} == {1, 2}
        assert {item.acoustic_profile for item in items} == {
            "clean",
            "office_noise",
            "compressed",
        }
        assert len(items) == 6


def test_asset_plan_final_expectations_are_independent_golden_slots() -> None:
    plan = build_routed_audio_asset_plan()
    by_group = {
        (item.category, item.lang): item.expected_ask_slot
        for item in plan
    }

    assert len(by_group) == 18
    assert by_group[("data_mismatch", "en")] == "owner"
    assert by_group[("manual_work", "ja")] == "success_condition"
    assert by_group[("integration_failure", "en")] is None
    assert by_group[("data_mismatch", "ko")] == "owner"
    assert by_group[("manual_work", "ko")] == "business_impact"
