"""Unit tests for ``resolve_masked_api_keys``.

The resolver restores real API keys when the client echoes back the masked
placeholder, while leaving genuinely new keys untouched. It must handle adds,
removes, reorders, and partial replacements across both scalar and list values.
"""

from api.services.configuration.masking import (
    _secret_values_differ,
    mask_key,
    resolve_masked_api_keys,
)

REAL_A = "sk-real-key-aaaaaaaaaaaa1111"
REAL_B = "sk-real-key-bbbbbbbbbbbb2222"
REAL_C = "sk-real-key-cccccccccccc3333"
REAL_D_SMALL = "ABCDE"

MASK_A = mask_key(REAL_A)
MASK_B = mask_key(REAL_B)
MASK_C = mask_key(REAL_C)
MASK_D_SMALL = mask_key(REAL_D_SMALL)


class TestScalar:
    def test_masked_scalar_restores_existing(self):
        assert resolve_masked_api_keys(MASK_A, REAL_A) == REAL_A

    def test_unmasked_scalar_is_kept(self):
        new_key = "sk-brand-new-real-key-9999"
        assert resolve_masked_api_keys(new_key, REAL_A) == new_key

    def test_mask_of_different_key_is_kept_verbatim(self):
        # Incoming is a mask, but not the mask of the existing key -> kept as-is.
        assert resolve_masked_api_keys(MASK_B, REAL_A) == MASK_B

    def test_small_mask_is_resolved(self):
        assert resolve_masked_api_keys(MASK_D_SMALL, REAL_D_SMALL) == REAL_D_SMALL


class TestList:
    def test_all_masked_restores_all_reals(self):
        result = resolve_masked_api_keys([MASK_A, MASK_B], [REAL_A, REAL_B])
        assert result == [REAL_A, REAL_B]

    def test_reorder_is_preserved(self):
        # Client sent the keys back in a different order than stored.
        result = resolve_masked_api_keys([MASK_B, MASK_A], [REAL_A, REAL_B])
        assert result == [REAL_B, REAL_A]

    def test_new_key_added_alongside_masked(self):
        new_key = "sk-brand-new-real-key-9999"
        result = resolve_masked_api_keys([MASK_A, new_key], [REAL_A])
        assert result == [REAL_A, new_key]

    def test_key_removed(self):
        # Only one of two stored keys is echoed back.
        result = resolve_masked_api_keys([MASK_B], [REAL_A, REAL_B])
        assert result == [REAL_B]

    def test_partial_replacement(self):
        new_key = "sk-brand-new-real-key-9999"
        result = resolve_masked_api_keys(
            [MASK_A, new_key, MASK_C], [REAL_A, REAL_B, REAL_C]
        )
        assert result == [REAL_A, new_key, REAL_C]

    def test_unmasked_keys_kept_verbatim(self):
        result = resolve_masked_api_keys([REAL_A, REAL_B], [REAL_C])
        assert result == [REAL_A, REAL_B]


class TestUsedDeduplication:
    def test_each_existing_key_consumed_at_most_once(self):
        # Two identical masked placeholders, but only one matching real key
        # exists. The first consumes REAL_A; the second has no unused match and
        # is therefore kept verbatim rather than duplicating the real key.
        result = resolve_masked_api_keys([MASK_A, MASK_A], [REAL_A])
        assert result == [REAL_A, MASK_A]

    def test_two_identical_masks_two_identical_reals(self):
        result = resolve_masked_api_keys([MASK_A, MASK_A], [REAL_A, REAL_A])
        assert result == [REAL_A, REAL_A]


class TestMixedTypes:
    def test_scalar_incoming_list_existing(self):
        # A masked scalar resolved against a list of stored keys.
        result = resolve_masked_api_keys(MASK_B, [REAL_A, REAL_B])
        assert result == [REAL_B]

    def test_list_incoming_scalar_existing(self):
        result = resolve_masked_api_keys([MASK_A], REAL_A)
        assert result == [REAL_A]


class TestEmpty:
    def test_empty_incoming_list(self):
        assert resolve_masked_api_keys([], [REAL_A]) == []

    def test_no_existing_keys_keeps_incoming(self):
        result = resolve_masked_api_keys([MASK_A, MASK_B], [])
        assert result == [MASK_A, MASK_B]


class TestSecretValuesDiffer:
    def test_same_scalar_values_do_not_differ(self):
        assert _secret_values_differ(REAL_A, REAL_A) is False

    def test_different_scalar_values_differ(self):
        assert _secret_values_differ(REAL_A, REAL_B) is True

    def test_same_list_values_do_not_differ(self):
        assert _secret_values_differ([REAL_A, REAL_B], [REAL_A, REAL_B]) is False

    def test_same_list_values_in_different_order_differ(self):
        assert _secret_values_differ([REAL_A, REAL_B], [REAL_B, REAL_A]) is True

    def test_scalar_and_single_item_list_do_not_differ(self):
        assert _secret_values_differ(REAL_A, [REAL_A]) is False

    def test_scalar_and_multi_item_list_differ(self):
        assert _secret_values_differ(REAL_A, [REAL_A, REAL_B]) is True
