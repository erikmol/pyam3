from datetime import timedelta

DOMAIN = "mower_wifi"
DEFAULT_PORT = 8080
SCAN_INTERVAL = timedelta(seconds=30)
CONNECTION_STALE_TIMEOUT = timedelta(minutes=5)

# Duration in seconds for SetOverrideMow (start mowing override)
DEFAULT_MOW_DURATION = 14400  # 4 hours

PLATFORMS = ["lawn_mower", "sensor", "binary_sensor"]
