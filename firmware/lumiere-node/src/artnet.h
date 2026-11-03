// Art-Net 4, the parts a one-universe DMX output node needs: reading ArtDmx
// (light levels), answering ArtPoll (so Lightkey and QLC+ find the node) and
// building the ArtPollReply. Plain C++ with no Arduino calls, so the tests in
// ../test build it on a computer too.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace artnet {

constexpr uint16_t PORT = 6454;
constexpr uint16_t OP_POLL = 0x2000;
constexpr uint16_t OP_POLL_REPLY = 0x2100;
constexpr uint16_t OP_DMX = 0x5000;
constexpr size_t POLL_REPLY_SIZE = 239;
constexpr size_t DMX_CHANNELS = 512;

// The 8-byte "Art-Net\0" header and the little-endian op code after it.
inline uint16_t opcode(const uint8_t* pkt, size_t n) {
  if (n < 10 || memcmp(pkt, "Art-Net", 8) != 0) return 0;
  return pkt[8] | (pkt[9] << 8);
}

struct Dmx {
  uint16_t universe;     // 15-bit port address: net << 8 | sub-net << 4 | universe
  uint8_t sequence;
  uint16_t length;       // channels in this packet (2-512)
  const uint8_t* data;   // points into the packet
};

// An ArtDmx packet: ID, op code, protocol version (2), sequence, physical,
// sub-uni, net, length (big-endian), then the channel levels.
inline bool parseDmx(const uint8_t* pkt, size_t n, Dmx& out) {
  if (opcode(pkt, n) != OP_DMX || n < 18) return false;
  uint16_t length = (pkt[16] << 8) | pkt[17];
  if (length < 1 || length > DMX_CHANNELS || n < 18u + length) return false;
  out.universe = ((pkt[15] & 0x7F) << 8) | pkt[14];
  out.sequence = pkt[12];
  out.length = length;
  out.data = pkt + 18;
  return true;
}

struct NodeInfo {
  uint8_t ip[4];
  uint8_t mac[6];
  uint16_t universe;
  bool dhcp;            // the address came from a DHCP server
  bool sending;         // DMX is going out with live data
  const char* shortName;
  const char* longName;
  const char* report;
};

// Fills `buf` (at least POLL_REPLY_SIZE bytes) and returns the length.
inline size_t buildPollReply(uint8_t* buf, const NodeInfo& n) {
  memset(buf, 0, POLL_REPLY_SIZE);
  memcpy(buf, "Art-Net", 8);
  buf[8] = OP_POLL_REPLY & 0xFF;
  buf[9] = OP_POLL_REPLY >> 8;
  memcpy(buf + 10, n.ip, 4);
  buf[14] = PORT & 0xFF;               // port, little-endian
  buf[15] = PORT >> 8;
  buf[17] = 1;                         // firmware version
  buf[18] = (n.universe >> 8) & 0x7F;  // net switch
  buf[19] = (n.universe >> 4) & 0x0F;  // sub-net switch
  buf[23] = 0xD0;                      // status 1: indicators normal, set up from the network
  strncpy(reinterpret_cast<char*>(buf + 26), n.shortName, 17);
  strncpy(reinterpret_cast<char*>(buf + 44), n.longName, 63);
  strncpy(reinterpret_cast<char*>(buf + 108), n.report, 63);
  buf[173] = 1;                        // one port
  buf[174] = 0x80;                     // it outputs DMX512
  buf[182] = n.sending ? 0x80 : 0x00;  // good output: data is being sent
  buf[190] = n.universe & 0x0F;        // SwOut for that port
  buf[200] = 0x00;                     // style: a DMX node
  memcpy(buf + 201, n.mac, 6);
  memcpy(buf + 207, n.ip, 4);          // bind IP
  buf[211] = 1;                        // bind index
  buf[212] = 0x08 | 0x04 | (n.dhcp ? 0x02 : 0);  // 15-bit addresses, DHCP capable, DHCP used
  return POLL_REPLY_SIZE;
}

}  // namespace artnet
