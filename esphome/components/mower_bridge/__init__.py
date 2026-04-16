import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import uart
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["uart", "network"]

mower_bridge_ns = cg.esphome_ns.namespace("mower_bridge")
MowerBridgeComponent = mower_bridge_ns.class_(
    "MowerBridgeComponent", cg.Component, uart.UARTDevice
)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(MowerBridgeComponent),
            cv.Optional(CONF_PORT, default=8080): cv.port,
        }
    )
    .extend(uart.UART_DEVICE_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)
    cg.add(var.set_port(config[CONF_PORT]))
