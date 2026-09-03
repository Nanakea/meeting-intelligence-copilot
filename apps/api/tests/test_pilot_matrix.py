from app.evals.pilot_matrix import platform_path_matrix, routed_audio_matrix


def test_routed_audio_acceptance_matrix_has_108_unique_scenarios() -> None:
    matrix = routed_audio_matrix()
    assert len(matrix) == 108
    assert len({scenario.scenario_id for scenario in matrix}) == 108
    assert all(scenario.lang in {"ja", "en", "ko"} for scenario in matrix)


def test_platform_path_acceptance_matrix_has_24_unique_scenarios() -> None:
    matrix = platform_path_matrix()
    assert len(matrix) == 24
    assert len({scenario.scenario_id for scenario in matrix}) == 24
    assert {scenario.platform for scenario in matrix} == {
        "zoom_desktop",
        "google_meet_browser",
        "teams_desktop",
    }
    expected_sources = {
        1: ("data_mismatch", "clean", "-clean-1"),
        2: ("manual_work", "office_noise", "-office_noise-2"),
        3: ("integration_failure", "compressed", "-compressed-1"),
    }
    for scenario in (item for item in matrix if item.lang != "ko"):
        category, profile, suffix = expected_sources[scenario.seed]
        assert scenario.category == category
        assert scenario.acoustic_profile == profile
        assert scenario.source_audio_scenario_id.startswith(
            f"audio-{category}-{scenario.lang}-"
        )
        assert scenario.source_audio_scenario_id.endswith(suffix)
    korean = [scenario for scenario in matrix if scenario.lang == "ko"]
    assert len(korean) == 6
    assert {scenario.category for scenario in korean} == {"data_mismatch", "no_pain"}
