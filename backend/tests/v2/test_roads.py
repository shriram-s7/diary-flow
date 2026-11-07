import pytest
from pydantic import ValidationError

from v2.catalog.roads import Location, RoadError, validate_location


def result(**changes):
    return {'road_lat': 13.148, 'road_lon': 80.231, 'snap_distance_m': 2,
            'road_name': 'Fixture road', **changes}


def validate(value):
    return validate_location(Location(lat=13.148, lon=80.231),
        snapper=lambda *a, **kw: value, provider_url='https://user:secret@road.example/path?key=secret')


def test_valid_location_preserves_raw_snapped_and_redacts_provenance():
    item = validate(result())
    assert item['raw'] == item['snapped'] == {'lat': 13.148, 'lon': 80.231}
    assert item['snap_distance_m'] == 2
    assert item['validated'] is True
    assert item['provenance']['origin'] == 'https://road.example'
    assert 'secret' not in str(item)
    assert item['provenance']['provider_data_version'] is None


@pytest.mark.parametrize('distance,accepted', [(300, True), (300.0001, False)])
def test_distance_threshold_before_rounding(distance, accepted):
    if accepted:
        assert validate(result(snap_distance_m=distance))['validated']
    else:
        with pytest.raises(RoadError) as error:
            validate(result(snap_distance_m=distance))
        assert (error.value.status, error.value.code) == (422, 'road_too_far')


@pytest.mark.parametrize('value', [None, {}, result(road_lat=float('nan')), result(road_lon=181),
    result(snap_distance_m=-1), result(snap_distance_m=True), result(road_lat='13')])
def test_unavailable_or_bad_provider_never_approves(value):
    with pytest.raises(RoadError) as error:
        validate(value)
    assert error.value.status == 503


def test_far_returned_coordinate_not_hidden_by_wrong_distance():
    with pytest.raises(RoadError) as error:
        validate(result(road_lat=14, snap_distance_m=0))
    assert error.value.code == 'road_too_far'


def test_provider_exception_is_explicit_failure():
    def unavailable(*args, **kwargs):
        assert kwargs['budget_sec'] == 3.0
        raise TimeoutError('fixture')
    with pytest.raises(RoadError) as error:
        validate_location(Location(lat=13.148, lon=80.231), snapper=unavailable, provider_url='https://road.example')
    assert error.value.status == 503


@pytest.mark.parametrize('values', [{'lat': 91, 'lon': 80}, {'lat': 13, 'lon': -181},
    {'lat': float('nan'), 'lon': 80}, {'lat': '13', 'lon': 80}, {'lat': True, 'lon': 80}])
def test_invalid_input_rejected(values):
    with pytest.raises(ValidationError):
        Location(**values)
