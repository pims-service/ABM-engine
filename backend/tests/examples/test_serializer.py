"""Example: serializer layer. Plain DRF serializers need no database when given plain data."""

from rest_framework import serializers


class PingSerializer(serializers.Serializer):
    """Stand-in serializer; real ones live in each app's `serializers.py`."""

    name = serializers.CharField(max_length=10)
    count = serializers.IntegerField(min_value=1, default=1)


def test_valid_payload():
    serializer = PingSerializer(data={"name": "abm"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"name": "abm", "count": 1}


def test_invalid_payload_reports_field_errors():
    serializer = PingSerializer(data={"name": "x" * 11, "count": 0})
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"name", "count"}
