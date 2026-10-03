from decimal import Decimal

from adaptive_grid_manager.economic_validation import score_bucket


def test_score_buckets():
    assert score_bucket(Decimal("60")) == "60-65"
    assert score_bucket(Decimal("64.999")) == "60-65"
    assert score_bucket(Decimal("65")) == "65-70"
    assert score_bucket(Decimal("70")) == "70-75"
    assert score_bucket(Decimal("75")) == "75-80"
    assert score_bucket(Decimal("80")) == "80+"
