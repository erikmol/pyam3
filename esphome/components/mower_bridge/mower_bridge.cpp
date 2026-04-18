#include "mower_bridge.h"
#include "esphome/core/log.h"

#include <lwip/sockets.h>
#include <lwip/netdb.h>
#include <fcntl.h>
#include <cerrno>
#include <cstring>
#include <algorithm>

namespace esphome {
namespace mower_bridge {

static const char *const TAG = "mower_bridge";

// ── Component lifecycle ───────────────────────────────────────────────────────

void MowerBridgeComponent::setup() {
  setup_server_();
}

void MowerBridgeComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Mower Bridge:");
  ESP_LOGCONFIG(TAG, "  TCP port: %d", port_);
  ESP_LOGCONFIG(TAG, "  Server socket: %s", server_fd_ >= 0 ? "OK" : "FAILED");
}

void MowerBridgeComponent::loop() {
  // Retry server setup if it failed during setup() (e.g. network not yet ready).
  if (server_fd_ < 0) {
    setup_server_();
    return;
  }

  if (client_fd_ < 0) {
    try_accept_();
    return;
  }

  handle_tcp_to_uart_();
  handle_uart_to_tcp_();
}

// ── Server setup ──────────────────────────────────────────────────────────────

void MowerBridgeComponent::setup_server_() {
  server_fd_ = socket(AF_INET, SOCK_STREAM, 0);
  if (server_fd_ < 0) {
    ESP_LOGW(TAG, "socket() failed (errno %d) — will retry", errno);
    return;
  }

  int opt = 1;
  setsockopt(server_fd_, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

  // Non-blocking so accept() in loop() returns immediately when no client waits.
  fcntl(server_fd_, F_SETFL, O_NONBLOCK);

  struct sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = INADDR_ANY;
  addr.sin_port = htons(port_);

  if (bind(server_fd_, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) < 0) {
    ESP_LOGW(TAG, "bind() failed (errno %d) — will retry", errno);
    close(server_fd_);
    server_fd_ = -1;
    return;
  }

  if (listen(server_fd_, 1) < 0) {
    ESP_LOGW(TAG, "listen() failed (errno %d) — will retry", errno);
    close(server_fd_);
    server_fd_ = -1;
    return;
  }

  ESP_LOGI(TAG, "TCP server listening on port %d", port_);
}

// ── Accept ────────────────────────────────────────────────────────────────────

void MowerBridgeComponent::try_accept_() {
  struct sockaddr_in client_addr{};
  socklen_t addr_len = sizeof(client_addr);
  int fd = accept(server_fd_, reinterpret_cast<struct sockaddr *>(&client_addr), &addr_len);

  if (fd < 0) {
    if (errno != EAGAIN && errno != EWOULDBLOCK) {
      ESP_LOGW(TAG, "accept() error (errno %d)", errno);
    }
    return;
  }

  // If a client is already connected, replace it with the new one so that a
  // Python-side reconnect is always accepted without restarting the bridge.
  if (client_fd_ >= 0) {
    ESP_LOGI(TAG, "New connection received — replacing existing client");
    close_client_();
  }

  fcntl(fd, F_SETFL, O_NONBLOCK);

  int opt = 1;
  setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &opt, sizeof(opt));

  client_fd_ = fd;
  tcp_buf_len_ = 0;

  char ip_str[INET_ADDRSTRLEN];
  inet_ntop(AF_INET, &client_addr.sin_addr, ip_str, sizeof(ip_str));
  ESP_LOGI(TAG, "Client connected from %s:%d", ip_str, ntohs(client_addr.sin_port));
}

// ── TCP → UART forwarding ─────────────────────────────────────────────────────

void MowerBridgeComponent::handle_tcp_to_uart_() {
  uint8_t recv_buf[256];
  ssize_t n = recv(client_fd_, recv_buf, sizeof(recv_buf), MSG_DONTWAIT);

  if (n < 0) {
    if (errno == EAGAIN || errno == EWOULDBLOCK) return;
    ESP_LOGW(TAG, "TCP recv error (errno %d), closing client", errno);
    close_client_();
    return;
  }
  if (n == 0) {
    ESP_LOGI(TAG, "TCP client disconnected");
    close_client_();
    return;
  }

  // Append to accumulator; guard against overflow.
  if (tcp_buf_len_ + static_cast<size_t>(n) > sizeof(tcp_buf_)) {
    ESP_LOGW(TAG, "TCP accumulator overflow — resetting buffer");
    tcp_buf_len_ = 0;
  }
  memcpy(tcp_buf_ + tcp_buf_len_, recv_buf, n);
  tcp_buf_len_ += n;

  // Extract and dispatch complete frames.
  while (tcp_buf_len_ > 0) {
    // Sync to STX (0x02) — discard any leading garbage.
    if (tcp_buf_[0] != 0x02) {
      size_t stx = 0;
      while (stx < tcp_buf_len_ && tcp_buf_[stx] != 0x02) stx++;
      if (stx > 0) {
        memmove(tcp_buf_, tcp_buf_ + stx, tcp_buf_len_ - stx);
        tcp_buf_len_ -= stx;
      }
      if (tcp_buf_len_ == 0) break;
    }

    // Intercept bridge heartbeat before frame_length_() — the heartbeat bytes
    // would be misparsed as a Simple protocol frame (marker=0x50, length=73).
    if (tcp_buf_len_ >= HEARTBEAT_LEN &&
        memcmp(tcp_buf_, HEARTBEAT_PING, HEARTBEAT_LEN) == 0) {
      ESP_LOGD(TAG, "Heartbeat PING → PONG");
      send(client_fd_, HEARTBEAT_PONG, HEARTBEAT_LEN, MSG_DONTWAIT);
      memmove(tcp_buf_, tcp_buf_ + HEARTBEAT_LEN, tcp_buf_len_ - HEARTBEAT_LEN);
      tcp_buf_len_ -= HEARTBEAT_LEN;
      continue;
    }

    int flen = frame_length_(tcp_buf_, tcp_buf_len_);
    if (flen < 0) break;  // incomplete header — wait for more bytes

    if (static_cast<size_t>(flen) > MAX_FRAME_SIZE) {
      // Implausible length — drop this STX and resync.
      ESP_LOGW(TAG, "Implausible frame length %d, resyncing", flen);
      memmove(tcp_buf_, tcp_buf_ + 1, tcp_buf_len_ - 1);
      tcp_buf_len_--;
      continue;
    }

    if (static_cast<size_t>(flen) > tcp_buf_len_) break;  // frame incomplete

    ESP_LOGD(TAG, "TCP→UART frame: %d bytes (marker=0x%02X)", flen, tcp_buf_[1]);
    write_array(tcp_buf_, flen);

    memmove(tcp_buf_, tcp_buf_ + flen, tcp_buf_len_ - flen);
    tcp_buf_len_ -= flen;
  }
}

// ── UART → TCP forwarding ─────────────────────────────────────────────────────

void MowerBridgeComponent::handle_uart_to_tcp_() {
  // Drain the UART RX buffer in chunks and forward to the TCP client.
  // The Python client owns frame reassembly on this path, so raw bytes are fine.
  uint8_t buf[64];
  while (available()) {
    size_t to_read = std::min(static_cast<size_t>(available()), sizeof(buf));
    read_array(buf, to_read);

    ESP_LOGD(TAG, "UART→TCP: %d bytes", (int)to_read);
    ssize_t sent = send(client_fd_, buf, to_read, MSG_DONTWAIT);
    if (sent < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
      ESP_LOGW(TAG, "TCP send error (errno %d), closing client", errno);
      close_client_();
      return;
    }
  }
}

// ── Client teardown ───────────────────────────────────────────────────────────

void MowerBridgeComponent::close_client_() {
  if (client_fd_ >= 0) {
    close(client_fd_);
    client_fd_ = -1;
  }
  tcp_buf_len_ = 0;
}

// ── Frame length helper ───────────────────────────────────────────────────────

int MowerBridgeComponent::frame_length_(const uint8_t *buf, size_t avail) {
  if (avail < 3) return -1;
  uint8_t marker = buf[1];
  if (marker == 0x81 || marker == 0xFD) {
    // Extended / Linked protocol: remaining length in bytes [2:4] little-endian.
    // total = remaining + 4 (STX + marker + remaining_lo + remaining_hi)
    if (avail < 4) return -1;
    uint16_t remaining = static_cast<uint16_t>(buf[2]) | (static_cast<uint16_t>(buf[3]) << 8);
    return static_cast<int>(remaining) + 4;
  }
  // Simple protocol: payload length in byte[2].
  // total = payload_len + 5 (STX + major + payload_len + CRC + ETX)
  return static_cast<int>(buf[2]) + 5;
}

}  // namespace mower_bridge
}  // namespace esphome
