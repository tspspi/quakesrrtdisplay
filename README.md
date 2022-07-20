# QUAK/ESR realtime data display

Quick and dirty visualization of data published by a specific experimental
setup via MQTT

## Installation

```
pip install quakesrrtdisplay-tspspi
```

_Note_: On our experimental systems this is deployed automatically by
Jenkins also on updates.

## Default configuration

The connection dialog can be filled with default credentials. This
is done by a ```~/.config/quakesrdisplay/connection.conf``` that contains
a simple JSON structure supplying the values (password also not encrypted):

```
{
        "broker" : '127.0.0.1',
        "port" : '1883',
        "user" : 'someMQTTusername',
        "password" : 'anyPassword',
        "basetopic" : 'quakesr/experiment'
}
```
