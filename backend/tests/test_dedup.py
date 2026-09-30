from app.services.dedup import _is_near_duplicate, dedup_key_for, dedup_within_batch
from app.services.sources.base import RawVideo


def test_dedup_key_uses_platform_video_id_when_present():
    video = RawVideo("instagram", "abc123", "https://ig.com/abc123", None, None)
    assert dedup_key_for(video) == "instagram:abc123"


def test_dedup_key_falls_back_to_url_hash_when_no_id():
    video = RawVideo("meta", "", "https://example.com/ad/1", None, None)
    key = dedup_key_for(video)
    assert key.startswith("meta:")
    assert len(key.split(":")[1]) == 64  # sha256 hex digest


def test_dedup_key_is_stable_for_same_url():
    v1 = RawVideo("meta", "", "https://example.com/ad/1", None, None)
    v2 = RawVideo("meta", "", "https://example.com/ad/1", None, None)
    assert dedup_key_for(v1) == dedup_key_for(v2)


def test_near_duplicate_detection_identical_hash():
    assert _is_near_duplicate("ffff0000ffff0000", "ffff0000ffff0000") is True


def test_near_duplicate_detection_very_different_hash():
    assert _is_near_duplicate("ffffffffffffffff", "0000000000000000") is False


def test_dedup_within_batch_drops_exact_id_repeat():
    v1 = RawVideo("meta", "ad_1", "https://example.com/a", None, None)
    v2 = RawVideo("meta", "ad_1", "https://example.com/a-mirror", None, None)  # same ad ID, different URL
    candidates = [(v1, dedup_key_for(v1), None), (v2, dedup_key_for(v2), None)]

    kept = dedup_within_batch(candidates)

    assert len(kept) == 1
    assert kept[0][0] is v1


def test_dedup_within_batch_drops_near_duplicate_thumbnail():
    v1 = RawVideo("meta", "ad_1", "https://example.com/a", "thumb1", None)
    v2 = RawVideo("meta", "ad_2", "https://example.com/b", "thumb2", None)  # different ID, same repost
    candidates = [
        (v1, dedup_key_for(v1), "ffff0000ffff0000"),
        (v2, dedup_key_for(v2), "ffff0000ffff0004"),  # 1 bit off -> within HAMMING_THRESHOLD
    ]

    kept = dedup_within_batch(candidates)

    assert len(kept) == 1
    assert kept[0][0] is v1


def test_dedup_within_batch_keeps_visually_distinct_videos():
    v1 = RawVideo("instagram", "r1", "https://example.com/1", "thumb1", None)
    v2 = RawVideo("instagram", "r2", "https://example.com/2", "thumb2", None)
    candidates = [
        (v1, dedup_key_for(v1), "ffffffffffffffff"),
        (v2, dedup_key_for(v2), "0000000000000000"),
    ]

    kept = dedup_within_batch(candidates)

    assert len(kept) == 2
