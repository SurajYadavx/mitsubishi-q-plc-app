"""
Unit tests for Mitsubishi Q-Series PLC Gateway logic.
Runs offline without hardware or live serial ports.
"""
import sys
import json

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from plc_reader import _device_to_addr, _addr_to_label, DeviceReading, PLCReader
from serial_publisher import SerialPublisher

def test_address_conversion():
    assert _device_to_addr('X0') == ('X', 0)
    assert _device_to_addr('X7') == ('X', 7)
    assert _device_to_addr('Y20') == ('Y', 32)
    assert _device_to_addr('Y27') == ('Y', 39)

    assert _addr_to_label('X', 0) == 'X0'
    assert _addr_to_label('X', 7) == 'X7'
    assert _addr_to_label('Y', 32) == 'Y20'
    assert _addr_to_label('Y', 39) == 'Y27'

def test_device_reading():
    reading = DeviceReading(
        timestamp_iso='2026-10-15T12:00:00Z',
        inputs={'X0': 1, 'X1': 0, 'X2': 0, 'X3': 1, 'X4': 0, 'X5': 0, 'X6': 0, 'X7': 0},
        outputs={'Y20': 1, 'Y21': 0, 'Y22': 0, 'Y23': 0, 'Y24': 0, 'Y25': 0, 'Y26': 0, 'Y27': 0},
        connected=True,
    )
    d = reading.__dict__
    assert d['inputs']['X0'] == 1
    assert d['inputs']['X3'] == 1
    assert d['outputs']['Y20'] == 1
    assert d['outputs']['Y21'] == 0
    assert d['connected'] is True
    assert 'timestamp_iso' in d

def test_serial_publisher_telemetry_format():
    sp = SerialPublisher(port='COM99', baud_rate=115200, gateway_id='test-gw')
    reading = DeviceReading(
        timestamp_iso='2026-10-15T12:00:00Z',
        inputs={'X0': 1, 'X3': 1},
        outputs={'Y20': 0},
        connected=True,
    )
    payload = sp.build_reading_payload(reading)
    line = sp.format_payload(payload)
    assert line.endswith('\n')
    parsed = json.loads(line.strip())
    assert parsed['type'] == 'plc_reading'
    assert parsed['gw_id'] == 'test-gw'
    assert parsed['connected'] is True
    assert parsed['inputs']['X0'] == 1
    assert parsed['outputs']['Y20'] == 0

def test_serial_publisher_error_format():
    sp = SerialPublisher(port='COM99', baud_rate=115200, gateway_id='test-gw')
    reading = DeviceReading(
        timestamp_iso='2026-10-15T12:00:00Z',
        connected=False,
        error='PLC connection timeout',
    )
    payload = sp.build_reading_payload(reading)
    line = sp.format_payload(payload)
    assert line.endswith('\n')
    parsed = json.loads(line.strip())
    assert parsed['type'] == 'gateway_error'
    assert parsed['connected'] is False
    assert parsed['error'] == 'PLC connection timeout'
    assert 'inputs' not in parsed

def test_serial_publisher_heartbeat_format():
    sp = SerialPublisher(port='COM99', baud_rate=115200, gateway_id='test-gw')
    payload = sp.build_heartbeat_payload(connected=True)
    line = sp.format_payload(payload)
    assert line.endswith('\n')
    parsed = json.loads(line.strip())
    assert parsed['type'] == 'heartbeat'
    assert parsed['connected'] is True
    assert parsed['gw_id'] == 'test-gw'

def test_plc_reader_config():
    reader = PLCReader(ip='192.168.3.39', port=5010, timeout=3.5)
    assert reader.timeout == 3.5
    assert reader.ip == '192.168.3.39'
    assert reader.port == 5010
    assert reader.connected is False

def test_config_settings_separation():
    import configparser
    cfg_file = Path(__file__).resolve().parent / 'config' / 'settings.ini'
    assert cfg_file.exists(), "settings.ini must exist"
    cfg = configparser.ConfigParser()
    cfg.read(str(cfg_file), encoding='utf-8')

    # Verify section separation
    assert cfg.has_section('plc')
    assert cfg.has_section('serial')
    assert cfg.has_section('devices')
    assert cfg.has_section('gateway')

    # Distinct port settings: PLC TCP port vs Serial COM port
    plc_port = cfg.getint('plc', 'port')
    serial_port = cfg.get('serial', 'port')
    assert plc_port == 5010, f"Expected PLC port 5010, got {plc_port}"
    assert serial_port == 'COM9', f"Expected serial COM9, got {serial_port}"
    assert isinstance(plc_port, int)
    assert isinstance(serial_port, str)

    # Verify device point lists
    inputs = [d.strip() for d in cfg.get('devices', 'inputs').split(',')]
    outputs = [d.strip() for d in cfg.get('devices', 'outputs').split(',')]
    assert len(inputs) == 8
    assert len(outputs) == 8
    assert inputs == ['X0','X1','X2','X3','X4','X5','X6','X7']
    assert outputs == ['Y20','Y21','Y22','Y23','Y24','Y25','Y26','Y27']

if __name__ == '__main__':
    test_address_conversion()
    test_device_reading()
    test_serial_publisher_telemetry_format()
    test_serial_publisher_error_format()
    test_serial_publisher_heartbeat_format()
    test_plc_reader_config()
    test_config_settings_separation()
    print('All 7 Python test suites passed successfully!')

