"""Constants for the Helianthus integration."""

DOMAIN = "helianthus"
MDNS_SERVICE_TYPE = "_helianthus-graphql._tcp.local."

CONF_PATH = "path"
CONF_TRANSPORT = "transport"
CONF_VERSION = "version"
CONF_INSTANCE_GUID = "instance_guid"
CONF_HOST_ALIASES = "host_aliases"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_USE_SUBSCRIPTIONS = "use_subscriptions"
CONF_ZONE_SCHEDULE_HELPERS = "zone_schedule_helpers"
CONF_DHW_SCHEDULE_HELPER = "dhw_schedule_helper"
CONF_PV_M2M_ENABLED = "pv_m2m_enabled"
CONF_PV_M2M_ENDPOINT = "pv_m2m_endpoint"
CONF_PV_M2M_ASSET_REF = "pv_m2m_asset_ref"
CONF_PV_M2M_CA_CERT_FILE = "pv_m2m_ca_cert_file"
CONF_PV_M2M_CLIENT_CERT_FILE = "pv_m2m_client_cert_file"
CONF_PV_M2M_CLIENT_KEY_FILE = "pv_m2m_client_key_file"
CONF_PV_M2M_DESCRIPTORS = "pv_m2m_descriptors"

DEFAULT_GRAPHQL_PATH = "/graphql"
DEFAULT_GRAPHQL_TRANSPORT = "http"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_USE_SUBSCRIPTIONS = True
DEFAULT_ZONE_SCHEDULE_HELPERS = ""
DEFAULT_DHW_SCHEDULE_HELPER = ""
DEFAULT_PV_M2M_ENABLED = False
DEFAULT_PV_M2M_ENDPOINT = ""
DEFAULT_PV_M2M_ASSET_REF = ""
DEFAULT_PV_M2M_CA_CERT_FILE = ""
DEFAULT_PV_M2M_CLIENT_CERT_FILE = ""
DEFAULT_PV_M2M_CLIENT_KEY_FILE = ""
