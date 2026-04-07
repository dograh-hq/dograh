"""
TDD tests for campaign traffic splitting version selection.

The select_version() function performs weighted random selection across
workflow definition versions for A/B testing in campaigns.

Module under test: api.services.campaign.version_selector
"""

from collections import Counter

from api.services.campaign.version_selector import select_version

# ---------------------------------------------------------------------------
# No split → use current published (returns None)
# ---------------------------------------------------------------------------


class TestNoSplit:
    def test_none_config_returns_none(self):
        """No version_config means use the current published version."""
        assert select_version(None) is None

    def test_empty_dict_returns_none(self):
        assert select_version({}) is None

    def test_empty_versions_list_returns_none(self):
        assert select_version({"versions": []}) is None

    def test_missing_versions_key_returns_none(self):
        assert select_version({"other_key": "value"}) is None


# ---------------------------------------------------------------------------
# Single version → always returns that version
# ---------------------------------------------------------------------------


class TestSingleVersion:
    def test_single_version_always_selected(self):
        config = {"versions": [{"definition_id": 42, "weight": 100}]}
        for _ in range(50):
            assert select_version(config) == 42

    def test_single_version_any_weight(self):
        """Weight value doesn't matter with one version — always selected."""
        config = {"versions": [{"definition_id": 99, "weight": 1}]}
        assert select_version(config) == 99


# ---------------------------------------------------------------------------
# Two versions → weighted distribution
# ---------------------------------------------------------------------------


class TestWeightedSelection:
    def test_70_30_split_approximate_distribution(self):
        """Over many iterations, selection should roughly match weights."""
        config = {
            "versions": [
                {"definition_id": 10, "weight": 70},
                {"definition_id": 20, "weight": 30},
            ]
        }
        counts = Counter(select_version(config) for _ in range(10_000))

        # With 10k samples, 70/30 should be within ±5% with very high probability
        ratio_10 = counts[10] / 10_000
        assert 0.60 < ratio_10 < 0.80, f"Expected ~70%, got {ratio_10:.1%}"

    def test_50_50_split(self):
        config = {
            "versions": [
                {"definition_id": 1, "weight": 50},
                {"definition_id": 2, "weight": 50},
            ]
        }
        counts = Counter(select_version(config) for _ in range(10_000))

        ratio = counts[1] / 10_000
        assert 0.40 < ratio < 0.60, f"Expected ~50%, got {ratio:.1%}"

    def test_weights_dont_need_to_sum_to_100(self):
        """Weights are relative — 3:1 is the same as 75:25."""
        config = {
            "versions": [
                {"definition_id": 1, "weight": 3},
                {"definition_id": 2, "weight": 1},
            ]
        }
        counts = Counter(select_version(config) for _ in range(10_000))

        ratio = counts[1] / 10_000
        assert 0.65 < ratio < 0.85, f"Expected ~75%, got {ratio:.1%}"


# ---------------------------------------------------------------------------
# Three or more versions
# ---------------------------------------------------------------------------


class TestMultipleVersions:
    def test_three_way_split(self):
        config = {
            "versions": [
                {"definition_id": 1, "weight": 50},
                {"definition_id": 2, "weight": 30},
                {"definition_id": 3, "weight": 20},
            ]
        }
        counts = Counter(select_version(config) for _ in range(10_000))

        assert 0.40 < counts[1] / 10_000 < 0.60
        assert 0.20 < counts[2] / 10_000 < 0.40
        assert 0.10 < counts[3] / 10_000 < 0.30

    def test_all_versions_can_be_selected(self):
        """Every version with weight > 0 should be reachable."""
        config = {
            "versions": [
                {"definition_id": 1, "weight": 1},
                {"definition_id": 2, "weight": 1},
                {"definition_id": 3, "weight": 1},
                {"definition_id": 4, "weight": 1},
            ]
        }
        selected = set(select_version(config) for _ in range(1_000))
        assert selected == {1, 2, 3, 4}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_zero_weight_version_never_selected(self):
        config = {
            "versions": [
                {"definition_id": 1, "weight": 100},
                {"definition_id": 2, "weight": 0},
            ]
        }
        selected = set(select_version(config) for _ in range(1_000))
        assert 2 not in selected

    def test_deterministic_with_seeded_random(self):
        """Selection should be deterministic when random is seeded."""
        config = {
            "versions": [
                {"definition_id": 10, "weight": 50},
                {"definition_id": 20, "weight": 50},
            ]
        }
        import random

        random.seed(42)
        result1 = select_version(config)
        random.seed(42)
        result2 = select_version(config)
        assert result1 == result2
