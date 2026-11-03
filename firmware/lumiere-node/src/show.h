// What the Node puts out on DMX, moment to moment. Plain C++ with no Arduino
// calls, so the tests in ../test run it on a computer with a fake clock.
//
//   LIVE     Art-Net is arriving: its levels go straight out.
//   HOLD     Art-Net stopped (a crash, a pulled cable): keep the last look for
//            hold_ms, in case it comes straight back.
//   FADING   Still nothing: fade to the house look over fade_ms.
//   HOUSE    The house look. Also where it starts at power-on, and where the
//            HOUSE button sends it (press again to go back to live).
//
// The room never snaps to black: every way out of live ends in the house look.
#pragma once

#include <stdint.h>
#include <string.h>

namespace show {

constexpr int CHANNELS = 512;
constexpr uint32_t SIGNAL_MS = 1000;  // Art-Net counts as arriving if seen within this

enum Mode : uint8_t { LIVE, HOLD, FADING, HOUSE };

inline const char* modeName(Mode m) {
  switch (m) {
    case LIVE: return "live";
    case HOLD: return "holding the last look";
    case FADING: return "fading to the house look";
    default: return "house look";
  }
}

class Show {
 public:
  uint32_t hold_ms = 10000;
  uint32_t fade_ms = 3000;
  uint8_t house[CHANNELS] = {0};
  uint8_t out[CHANNELS] = {0};

  // New levels from Art-Net (channels 1..length).
  void onArtnet(const uint8_t* data, int length, uint32_t now) {
    if (length > CHANNELS) length = CHANNELS;
    memcpy(live_, data, length);
    last_artnet_ = now;
    ever_live_ = true;
  }

  // The HOUSE button: true fades to the house look and stays there.
  void setHouseForced(bool on, uint32_t now) {
    if (on != forced_) {
      forced_ = on;
      if (on) startFade(now);
    }
  }
  bool houseForced() const { return forced_; }
  bool signal(uint32_t now) const { return ever_live_ && now - last_artnet_ < SIGNAL_MS; }
  const uint8_t* lastLive() const { return live_; }

  // Work out `out` for this moment; returns the mode.
  Mode update(uint32_t now) {
    Mode want;
    if (forced_ || !ever_live_) {
      want = HOUSE;
    } else if (now - last_artnet_ < SIGNAL_MS) {
      want = LIVE;
    } else if (now - last_artnet_ < SIGNAL_MS + hold_ms) {
      want = HOLD;
    } else {
      want = HOUSE;
    }
    if (want != HOUSE) {
      fading_ = false;  // a fade cut short by live data mustn't linger
    } else if ((mode_ == LIVE || mode_ == HOLD) && !fading_) {
      startFade(now);   // leaving live: fade from what's on the lights now
    }
    if (want == HOUSE && fading_) {
      uint32_t t = now - fade_start_;
      if (t >= fade_ms || fade_ms == 0) {
        fading_ = false;
      } else {
        for (int i = 0; i < CHANNELS; i++)
          out[i] = from_[i] + (int32_t)(house[i] - from_[i]) * (int32_t)t / (int32_t)fade_ms;
        mode_ = FADING;
        return mode_;
      }
    }
    if (want == HOUSE) {
      memcpy(out, house, CHANNELS);
    } else {
      memcpy(out, live_, CHANNELS);  // live, or the last live look while holding
    }
    mode_ = want;
    return mode_;
  }

  Mode mode() const { return mode_; }

 private:
  void startFade(uint32_t now) {
    memcpy(from_, out, CHANNELS);
    fade_start_ = now;
    fading_ = true;
  }

  uint8_t live_[CHANNELS] = {0};
  uint8_t from_[CHANNELS] = {0};
  uint32_t last_artnet_ = 0;
  uint32_t fade_start_ = 0;
  bool ever_live_ = false;
  bool forced_ = false;
  bool fading_ = false;
  Mode mode_ = HOUSE;
};

// The DMX LED: solid while live, a slow blink while holding or in the house
// look, a fast blink when the Ethernet cable is out.
inline bool ledOn(Mode mode, bool network_up, uint32_t now) {
  if (!network_up) return (now / 125) % 2 == 0;  // 4 blinks a second
  if (mode == LIVE) return true;
  if (mode == HOUSE) return (now % 2000) < 150;  // a short flash every 2 s
  return (now / 500) % 2 == 0;                   // holding or fading: 1 blink a second
}

// The HOUSE button: a short press toggles the house look; holding it for
// LONG_PRESS_MS saves what's on the lights now as the house look.
constexpr uint32_t LONG_PRESS_MS = 3000;
constexpr uint32_t DEBOUNCE_MS = 30;

enum Press : uint8_t { NONE, SHORT_PRESS, LONG_PRESS };

class Button {
 public:
  // `down`: the button is pressed right now (raw, may bounce).
  Press update(bool down, uint32_t now) {
    if (down != raw_) {
      raw_ = down;
      changed_ = now;
    }
    if (now - changed_ < DEBOUNCE_MS || raw_ == stable_) {
      if (stable_ && !long_sent_ && now - pressed_at_ >= LONG_PRESS_MS) {
        long_sent_ = true;
        return LONG_PRESS;
      }
      return NONE;
    }
    stable_ = raw_;
    if (stable_) {
      pressed_at_ = now;
      long_sent_ = false;
      return NONE;
    }
    return long_sent_ ? NONE : SHORT_PRESS;  // released: short unless the long press already fired
  }

 private:
  bool raw_ = false, stable_ = false, long_sent_ = false;
  uint32_t changed_ = 0, pressed_at_ = 0;
};

}  // namespace show
