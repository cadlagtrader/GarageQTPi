#!/usr/bin/python3
import os
import random
import yaml
import paho.mqtt.client as mqtt
import paho.mqtt
import re
import json
import logging
import voluptuous as vol
from voluptuous import Any

from lib.garage import GarageDoor
from lib.garage import TwoSwitchGarageDoor
from lib.garage import MotionSensor

DEFAULT_DISCOVERY = False
DEFAULT_DISCOVERY_PREFIX = "homeassistant"
DEFAULT_AVAILABILITY_TOPIC = "home-assistant/cover/availabilty"
DEFAULT_PAYLOAD_AVAILABLE = "online"
DEFAULT_PAYLOAD_NOT_AVAILABLE ="offline"
DEFAULT_STATE_MODE = "normally_open"
DEFAULT_INVERT_RELAY = False
DEFAULT_CHECK_STATE_BEFORE_COMMAND = True #Check if the door state is closed before sending opening command (and opened before closing)
DEFAULT_DEVICE_CLASS = 'garage'

print("GarageQTPi starting")
discovery_info = {}
garage_objects = []

# Update the mqtt state topic


def update_state(value, topic):
    logging.info("State change triggered: %s -> %s" % (topic, value))

    client.publish(topic, value, retain=True)

# The callback for when the client receives a CONNACK response from the server.


def on_connect(client, userdata, flags, rc):
    logging.info("Connected with result code: %s" % mqtt.connack_string(rc))
    # notify subscribed clients that we are available
    client.publish(availability_topic, payload_available, retain=True)

    logging.info(
        "Sent payload: '" +
        CONFIG['mqtt']['payload_available'] +
        "' to topic: '" +
        CONFIG['mqtt']['availability_topic'] +
        "'")

    for config in CONFIG['doors']:
        command_topic = config['command_topic']
        logging.info("Listening for commands on %s" % command_topic)
        client.subscribe(command_topic)

    # Update each door state in case it changed while disconnected.
    for object in garage_objects:
        client.publish(object.state_topic, object.state, retain=True)

# Execute the specified command for a door


def execute_command(door, command):
    try:
        doorName = door.name
    except BaseException:
        doorName = door.id
    logging.info("Executing command %s for door %s" % (command, doorName))
    if command == "OPEN":
        door.open()
    elif command == "CLOSE":
        door.close()
    elif command == "STOP":
        door.stop()
    else:
        logging.info("Invalid command: %s" % command)



CONFIG_SCHEMA = vol.Schema(
    {
    "logging": vol.Schema(
        {
            vol.Required("log_level"): Any('DEBUG', 'INFO', 'WARNING','ERROR', 'CRITICAL'),
            vol.Required("show_timestamp"): bool
        }),
    "mqtt": vol.Schema(
        {
            vol.Required("host"): str,
            vol.Required("port"): int,
            vol.Required("user"): str,
            vol.Required("password"): str,
            vol.Optional("discovery", default = DEFAULT_DISCOVERY): Any(bool, None),
            vol.Optional("discovery_prefix", default = DEFAULT_DISCOVERY_PREFIX): Any(str, None),
            vol.Optional("availability_topic", default = DEFAULT_AVAILABILITY_TOPIC): Any(str, None),
            vol.Optional("payload_available", default = DEFAULT_PAYLOAD_AVAILABLE): Any(str,None),
            vol.Optional("payload_not_available", default = DEFAULT_PAYLOAD_NOT_AVAILABLE ): Any(str, None)


        }
    ),
    "sensors": [vol.Schema(
        {
            vol.Required("id"): str,
            vol.Optional("name"): Any(str, None), 
            vol.Required("state"): int,            
            vol.Optional("state_mode", default = DEFAULT_STATE_MODE): Any(None, 'normally_closed', 'normally_open'),
            vol.Optional("state_topic"): str,
            vol.Required("command_topic"): str,
            vol.Optional("device_class", default = DEFAULT_DEVICE_CLASS): str,
        }
    )],
    "doors": [vol.Schema(
        {
            vol.Required("id"): str,
            vol.Optional("name"): Any(str, None), 
            vol.Required("relay_opening"): int,
            vol.Required("relay_closing"): int,
            vol.Optional("relay_stop", default = None): Any(int,None),
            vol.Required("state"): int,
            vol.Optional("open"): int,
            vol.Optional("state_mode", default = DEFAULT_STATE_MODE): Any(None, 'normally_closed', 'normally_open'),
            vol.Optional("invert_relay", default = DEFAULT_INVERT_RELAY): bool,
            vol.Optional("check_state_before_command", default = DEFAULT_CHECK_STATE_BEFORE_COMMAND): bool,
            vol.Optional("state_topic"): str,
            vol.Required("command_topic"): str,
            vol.Optional("device_class", default = DEFAULT_DEVICE_CLASS): str,
        }
    )]
    })

#
# First look for config.yaml in /config which allows us to map a volume
# when running in docker.  If not there look in the directory the script is 
# running from. Using print statements here since logging isn't set up yet.
try:
    with open('/config/config.yaml', 'r') as ymlfile:
        file_CONFIG = yaml.load(ymlfile, Loader=yaml.FullLoader)
        print("using configuration from /config/config.yaml")
except FileNotFoundError:
    print("/config/config.yaml not found. Looking in script directory")
    try:
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config.yaml'), 'r') as ymlfile:
            file_CONFIG = yaml.load(ymlfile, Loader=yaml.FullLoader)
            print("Using config.yaml from script directory")
    except FileNotFoundError:
        print("No config.yaml found. SensorScanner exiting.")
        os._exit(1)

CONFIG = CONFIG_SCHEMA(file_CONFIG)
#
# setup logging and then log sucessful configuration validation
#
if CONFIG['logging']['show_timestamp']:
    logging.basicConfig(format='%(asctime)s %(message)s',level=CONFIG["logging"]["log_level"])
else:
    logging.basicConfig(level=CONFIG["logging"]["log_level"])

logging.info ("Config sucessfully validated against schema")
logging.info (json.dumps(CONFIG, indent = 4))

### SETUP MQTT ###
user = CONFIG['mqtt']['user']
password = CONFIG['mqtt']['password']
host = CONFIG['mqtt']['host']
port = int(CONFIG['mqtt']['port'])
if CONFIG['mqtt']['discovery'] is None:
    discovery = DEFAULT_DISCOVERY
else:
    discovery = CONFIG['mqtt']['discovery']

if CONFIG['mqtt']['discovery_prefix'] is None:
    discovery_prefix = DEFAULT_DISCOVERY_PREFIX
else:
    discovery_prefix = CONFIG['mqtt']['discovery_prefix']
    
#
# if availability values specified in config use them
# if not use defaults 
#

if CONFIG['mqtt']['availability_topic'] is None:
    availability_topic = DEFAULT_AVAILABILITY_TOPIC
else:
    availability_topic = CONFIG['mqtt']['availability_topic']

if CONFIG['mqtt']['payload_available'] is None:
    payload_available = DEFAULT_PAYLOAD_AVAILABLE
else:
    payload_available = CONFIG['mqtt']['payload_available']

if CONFIG['mqtt']['payload_not_available'] is None:
    payload_not_available = DEFAULT_PAYLOAD_NOT_AVAILABLE
else:
    payload_not_available = CONFIG['mqtt']['payload_not_available']

# client = mqtt.Client(client_id="MQTTGarageDoor_" + binascii.b2a_hex(os.urandom(6)), clean_session=True, userdata=None, protocol=4)
client = mqtt.Client(client_id="MQTTGarageDoor_{:6s}".format(str(random.randint(
    0, 999999))), clean_session=True, userdata=None, protocol=mqtt.MQTTv311)

client.on_connect = on_connect

client.username_pw_set(user, password=password)


# set a last will message so the broker will notify connected clients when
# we are not available
client.will_set(availability_topic, payload_not_available, retain=True)
logging.info(
    "Set last will message: '" +
    payload_not_available +
    "' for topic: '" +
    availability_topic +
    "'")


client.connect(host, port, 60)


### SETUP END ###

### MAIN LOOP ###
if __name__ == "__main__":
    # Create door objects and create callback functions
    for type, cfg in [('door', conf) for conf in CONFIG['doors']]+[('sensor', conf) for conf in CONFIG['sensors']]: 

        # If no name it set, then set to id
        if 'name' not in cfg:
            cfg['name'] = cfg['id']
        elif cfg['name'] is None:
            cfg['name'] = cfg['id']

        # Sanitize id value for mqtt
        cfg['id'] = re.sub(r'\W+', '', re.sub(r'\s', ' ', cfg['id']))

        if discovery is True:
            base_topic = discovery_prefix + "/cover/" + cfg['id']
            config_topic = base_topic + "/config"
            if type == 'door':
                cfg['command_topic'] = base_topic + "/set"
            cfg['state_topic'] = base_topic + "/state"

        
        if type == 'door':
            command_topic = cfg['command_topic']
        state_topic = cfg['state_topic']

        #
        # If the open switch is specified use a two switch garage door
        # otherwise use a door with only a closed switch.
        # The interface is the same.  The two switch garage door
        # reports the states "open" and "closed"
        #
        if type == 'door':
            if "open" in cfg and cfg["open"] is not None:
                object = TwoSwitchGarageDoor(cfg)
            else:
                object = GarageDoor(cfg)
        else:
            object = MotionSensor(cfg)


        # Callback per door that passes a reference to the door
        if type == 'door':
            def on_message(client, userdata, msg, door=object):
                execute_command(door, msg.payload.decode("utf-8"))
            
            client.message_callback_add(command_topic, on_message)

        # Callback per door that passes the doors state topic
        def on_state_change(value, topic=state_topic):
            update_state(value, topic)
        

        # You can add additional listeners here and they will all be executed
        # when the door state changes
        object.onStateChange.addHandler(on_state_change)

        # Publish initial door state
        client.publish(state_topic, object.state, retain=True)

        # Store Garage Door instance for use on reconnect
        object.state_topic = state_topic
        if type == 'door':
            object.command_topic = command_topic
        garage_objects.append(object)

        # If discovery is enabled publish configuration
        if discovery is True:

            discovery_info["name"] = cfg['name']
            if type == 'door':
                discovery_info["command_topic"] = cfg['command_topic']
                discovery_info["payload_available"] = payload_available
                discovery_info["payload_not_available"] = payload_not_available
                if cfg['relay_stop'] is None:
                    discovery_info["payload_stop"] = None
            discovery_info["state_topic"] = cfg['state_topic']
            discovery_info["availability_topic"] = availability_topic
            
            discovery_info["device_class"] = cfg['device_class']
            

            client.publish(
                config_topic,
                json.dumps(discovery_info),
                retain=True)

            logging.info(
                "Sent audodiscovery config: " +
                json.dumps(
                    discovery_info,
                    indent=4))
            logging.info("to topic: " + config_topic)

    # Main loop
    client.loop_forever()
