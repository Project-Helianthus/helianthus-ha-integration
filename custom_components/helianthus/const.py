"""Constants for the Helianthus integration."""

DOMAIN = "helianthus"
MDNS_SERVICE_TYPE = "_helianthus-graphql._tcp.local."

CONF_PATH = "path"
CONF_TRANSPORT = "transport"
CONF_VERSION = "version"
CONF_INSTANCE_GUID = "instance_guid"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_USE_SUBSCRIPTIONS = "use_subscriptions"
CONF_ZONE_SCHEDULE_HELPERS = "zone_schedule_helpers"
CONF_DHW_SCHEDULE_HELPER = "dhw_schedule_helper"

DEFAULT_GRAPHQL_PATH = "/graphql"
DEFAULT_GRAPHQL_TRANSPORT = "http"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_USE_SUBSCRIPTIONS = True
DEFAULT_ZONE_SCHEDULE_HELPERS = ""
DEFAULT_DHW_SCHEDULE_HELPER = ""
