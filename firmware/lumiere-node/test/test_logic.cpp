// Tests for the Node's Art-Net and show logic, built and run on a computer:
//   g++ -std=c++17 -I../src test_logic.cpp -o test_logic && ./test_logic
// (test_node.py at the repository root does this as part of pytest.)
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "artnet.h"
#include "show.h"

static int failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);       \
      failures++;                                                       \
    }                                                                   \
  } while (0)

static std::vector<uint8_t> artDmx(uint16_t universe, std::vector<uint8_t> levels, uint8_t seq = 1) {
  std::vector<uint8_t> p = {'A', 'r', 't', '-', 'N', 'e', 't', 0, 0x00, 0x50, 0, 14, seq, 0,
                            uint8_t(universe & 0xFF), uint8_t(universe >> 8),
                            uint8_t(levels.size() >> 8), uint8_t(levels.size() & 0xFF)};
  p.insert(p.end(), levels.begin(), levels.end());
  return p;
}

static void test_artnet() {
  auto pkt = artDmx(0x0102, {10, 20, 30, 40});
  artnet::Dmx d;
  CHECK(artnet::parseDmx(pkt.data(), pkt.size(), d));
  CHECK(d.universe == 0x0102 && d.length == 4 && d.data[0] == 10 && d.data[3] == 40);

  pkt.pop_back();  // shorter than it says
  CHECK(!artnet::parseDmx(pkt.data(), pkt.size(), d));
  auto junk = artDmx(0, {1, 2});
  junk[0] = 'X';
  CHECK(!artnet::parseDmx(junk.data(), junk.size(), d));

  uint8_t poll[14] = {'A', 'r', 't', '-', 'N', 'e', 't', 0, 0x00, 0x20, 0, 14, 0, 0};
  CHECK(artnet::opcode(poll, sizeof poll) == artnet::OP_POLL);

  uint8_t reply[artnet::POLL_REPLY_SIZE];
  artnet::NodeInfo info = {{2, 0, 0, 10}, {1, 2, 3, 4, 5, 6}, 0x0123, false, true,
                           "Lumiere Node", "Lumiere Node by Vital Acoustica", "OK"};
  size_t n = artnet::buildPollReply(reply, info);
  CHECK(n == 239);
  CHECK(artnet::opcode(reply, n) == artnet::OP_POLL_REPLY);
  CHECK(reply[10] == 2 && reply[13] == 10);        // IP
  CHECK(reply[14] == 0x36 && reply[15] == 0x19);   // port 6454, little-endian
  CHECK(reply[18] == 0x01 && reply[19] == 0x02 && reply[190] == 0x03);  // net 1, sub-net 2, universe 3
  CHECK(reply[174] == 0x80 && reply[182] == 0x80);  // one DMX output, sending
  CHECK(std::string(reinterpret_cast<char*>(reply + 26)) == "Lumiere Node");
  CHECK(reply[201] == 1 && reply[206] == 6);        // MAC
}

static void test_show() {
  show::Show s;
  s.hold_ms = 5000;
  s.fade_ms = 2000;
  s.house[0] = 200;  // house look: channel 1 at 200

  CHECK(s.update(0) == show::HOUSE && s.out[0] == 200);  // power-on: house look

  uint8_t look[3] = {50, 60, 70};
  s.onArtnet(look, 3, 1000);
  CHECK(s.update(1000) == show::LIVE && s.out[0] == 50 && s.out[2] == 70);
  CHECK(s.update(1900) == show::LIVE);

  // Art-Net stops at 1000: hold the last look...
  CHECK(s.update(2500) == show::HOLD && s.out[0] == 50);
  CHECK(s.update(6900) == show::HOLD && s.out[0] == 50);
  // ...then fade to the house look, never straight to black.
  CHECK(s.update(7000) == show::FADING && s.out[0] == 50);
  CHECK(s.update(8000) == show::FADING && s.out[0] == 125);  // halfway between 50 and 200
  CHECK(s.update(9000) == show::HOUSE && s.out[0] == 200 && s.out[1] == 0);

  // Art-Net comes back: live again at once.
  s.onArtnet(look, 3, 20000);
  CHECK(s.update(20000) == show::LIVE && s.out[0] == 50);

  // The HOUSE button fades to the house look and stays there while Art-Net keeps coming.
  s.setHouseForced(true, 20000);
  CHECK(s.update(21000) == show::FADING && s.out[0] == 125);
  s.onArtnet(look, 3, 22000);
  CHECK(s.update(22500) == show::HOUSE && s.out[0] == 200);
  s.setHouseForced(false, 23000);
  s.onArtnet(look, 3, 23000);
  CHECK(s.update(23000) == show::LIVE && s.out[0] == 50);

  // HOUSE pressed and released mid-fade, then the signal is lost: it still fades, no snap.
  show::Show c;
  c.hold_ms = 0;
  c.fade_ms = 2000;
  c.house[0] = 200;
  c.onArtnet(look, 3, 0);
  c.update(0);
  c.setHouseForced(true, 100);
  c.update(500);
  c.setHouseForced(false, 600);
  c.onArtnet(look, 3, 600);
  CHECK(c.update(600) == show::LIVE && c.out[0] == 50);
  CHECK(c.update(1600) == show::FADING && c.out[0] == 50);  // signal gone at 1600: the fade starts here
  CHECK(c.update(2600) == show::FADING && c.out[0] == 125);

  // The millisecond clock wraps after 49 days: still live.
  show::Show w;
  w.onArtnet(look, 3, 0xFFFFFF00u);
  CHECK(w.update(0x00000010u) == show::LIVE);
}

static void test_led_and_button() {
  CHECK(show::ledOn(show::LIVE, true, 1234));
  CHECK(show::ledOn(show::LIVE, false, 0) != show::ledOn(show::LIVE, false, 125));  // no network: blinks

  show::Button b;
  CHECK(b.update(true, 0) == show::NONE);
  CHECK(b.update(false, 10) == show::NONE);   // a 10 ms bounce is ignored
  CHECK(b.update(false, 100) == show::NONE);
  CHECK(b.update(true, 200) == show::NONE);
  CHECK(b.update(true, 240) == show::NONE);   // pressed (after debounce)
  CHECK(b.update(false, 400) == show::NONE);
  CHECK(b.update(false, 440) == show::SHORT_PRESS);

  show::Button h;
  h.update(true, 0);
  h.update(true, 40);
  CHECK(h.update(true, 2000) == show::NONE);
  CHECK(h.update(true, 3100) == show::LONG_PRESS);
  CHECK(h.update(true, 4000) == show::NONE);   // only once
  h.update(false, 5000);
  CHECK(h.update(false, 5040) == show::NONE);  // and no short press on release
}

int main() {
  test_artnet();
  test_show();
  test_led_and_button();
  if (failures) {
    std::printf("%d check(s) failed\n", failures);
    return 1;
  }
  std::printf("all Node logic tests passed\n");
  return 0;
}
