#pragma once

#include "esphome/core/component.h"
#include "esphome/components/uart/uart.h"

namespace esphome {
namespace mower_bridge {

// Bridge-layer heartbeat frames — never forwarded to the mower UART.
static const uint8_t HEARTBEAT_PING[] = {0x02, 0x50, 0x49, 0x4E, 0x47, 0x03};
static const uint8_t HEARTBEAT_PONG[] = {0x02, 0x50, 0x4F, 0x4E, 0x47, 0x03};
static const size_t HEARTBEAT_LEN = 6;

// Reject any frame whose embedded length field exceeds this value.
// All known mower frames are well under 100 bytes.
static const size_t MAX_FRAME_SIZE = 512;

class MowerBridgeComponent : public Component, public uart::UARTDevice {
 public:
  void set_port(uint16_t port) { port_ = port; }

  void setup() override;
  void loop() override;
  void dump_config() override;

  // Run after WiFi/network is up so the TCP bind succeeds immediately.
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

 private:
  uint16_t port_{8080};

  int server_fd_{-1};
  int client_fd_{-1};

  // Accumulates bytes received from the TCP client until a full frame is
  // assembled, so heartbeat frames can be intercepted before UART forwarding.
  uint8_t tcp_buf_[MAX_FRAME_SIZE * 2];
  size_t tcp_buf_len_{0};

  void setup_server_();
  void try_accept_();
  void handle_tcp_to_uart_();
  void handle_uart_to_tcp_();
  void close_client_();

  // Returns the expected total byte length of the frame starting at buf[0],
  // or -1 if there are not yet enough bytes to determine it.
  int frame_length_(const uint8_t *buf, size_t avail);
};

}  // namespace mower_bridge
}  // namespace esphome
