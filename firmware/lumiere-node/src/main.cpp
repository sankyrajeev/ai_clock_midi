// Lumière Node: Art-Net in over Ethernet, one universe of DMX out of the XLR.
// Runs on a WT32-ETH01 (ESP32 + LAN8720 Ethernet) wired to a MAX485 module as in
// hardware/WIRING.md. What goes out at each moment is decided in show.h; the
// Art-Net details are in artnet.h.
//
// Settings live on a web page at the Node's address (http://lumiere-node.local
// or its IP): Art-Net universe, network address, how long to hold the last look,
// the fade, and the house look. Updates after the first one go over the network.

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <ETH.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_task_wdt.h>

#include "artnet.h"
#include "show.h"

#ifndef NODE_VERSION
#define NODE_VERSION "1.0.0"
#endif

// --- pins (hardware/WIRING.md) ------------------------------------------------
constexpr int PIN_DMX_TX = 17;  // "TXD" on the board -> MAX485 DI
constexpr int PIN_DMX_DE = 33;  // "485_EN" -> MAX485 DE+RE: held high, we only send
constexpr int PIN_LED = 4;      // DMX LED through 220 ohms
constexpr int PIN_BUTTON = 14;  // HOUSE button to GND, internal pull-up

// WT32-ETH01's Ethernet chip. IO16 switches on the 50 MHz clock it runs from: it
// must be on before Ethernet starts, so it's driven here rather than handed to
// the driver as a reset pin (which leaves the clock off and fails with
// "emac reset timeout").
constexpr int ETH_ADDR = 1, ETH_CLOCK_ENABLE = 16, ETH_MDC = 23, ETH_MDIO = 18;

constexpr const char* HOSTNAME = "lumiere-node";
constexpr uint32_t DHCP_WAIT_MS = 6000;   // then fall back to the fixed address
constexpr uint32_t WATCHDOG_S = 8;        // restart if the program ever hangs this long

// --- settings, kept in flash ----------------------------------------------------
struct Settings {
  uint16_t universe = 0;
  bool dhcp = true;                         // false: always use the fixed address
  IPAddress ip = IPAddress(2, 0, 0, 10);    // fixed address (and the fallback without DHCP)
  IPAddress mask = IPAddress(255, 0, 0, 0);
  IPAddress gateway = IPAddress(0, 0, 0, 0);
  uint32_t hold_ms = 10000;
  uint32_t fade_ms = 3000;
  String ota_password = "lumiere-node";
} settings;

Preferences prefs;
show::Show stage;
SemaphoreHandle_t stage_lock;
WiFiUDP udp;
WebServer web(80);
show::Button button;

volatile bool eth_link = false;
volatile bool eth_has_ip = false;
bool eth_started = false;
bool services_started = false;
uint32_t eth_retry_at = 0;
bool using_fallback = false;
uint32_t link_up_at = 0;
uint32_t packets = 0, packets_window = 0, window_start = 0;
float packets_per_s = 0;
volatile show::Mode current_mode = show::HOUSE;

// ------------------------------------------------------------------------------
void loadSettings() {
  prefs.begin("node", false);
  settings.universe = prefs.getUShort("universe", settings.universe);
  settings.dhcp = prefs.getBool("dhcp", settings.dhcp);
  settings.ip = IPAddress(prefs.getUInt("ip", (uint32_t)settings.ip));
  settings.mask = IPAddress(prefs.getUInt("mask", (uint32_t)settings.mask));
  settings.gateway = IPAddress(prefs.getUInt("gw", (uint32_t)settings.gateway));
  settings.hold_ms = prefs.getUInt("hold", settings.hold_ms);
  settings.fade_ms = prefs.getUInt("fade", settings.fade_ms);
  if (prefs.isKey("otapw")) settings.ota_password = prefs.getString("otapw", settings.ota_password);
  if (prefs.isKey("house")) prefs.getBytes("house", stage.house, sizeof stage.house);  // dark until saved
  stage.hold_ms = settings.hold_ms;
  stage.fade_ms = settings.fade_ms;
}

void saveSettings() {
  prefs.putUShort("universe", settings.universe);
  prefs.putBool("dhcp", settings.dhcp);
  prefs.putUInt("ip", (uint32_t)settings.ip);
  prefs.putUInt("mask", (uint32_t)settings.mask);
  prefs.putUInt("gw", (uint32_t)settings.gateway);
  prefs.putUInt("hold", settings.hold_ms);
  prefs.putUInt("fade", settings.fade_ms);
  prefs.putString("otapw", settings.ota_password);
}

// Whatever is on the lights right now becomes the house look.
void saveHouseLook() {
  xSemaphoreTake(stage_lock, portMAX_DELAY);
  memcpy(stage.house, stage.out, sizeof stage.house);
  xSemaphoreGive(stage_lock);
  prefs.putBytes("house", stage.house, sizeof stage.house);
  Serial.println("House look saved");
}

// --- DMX: its own task, so web pages and updates never stall the lights --------
// A DMX frame: a break (line low >= 88 us), mark-after-break, start code 0, then
// 512 levels at 250 kbaud, 8N2. The break is a 0 byte sent at a slower speed.
void sendFrame(const uint8_t* levels) {
  Serial2.flush();
  Serial2.updateBaudRate(83333);  // 0x00 at 83.3 kbaud: 108 us low, then 24 us high
  Serial2.write((uint8_t)0);
  Serial2.flush();
  Serial2.updateBaudRate(250000);
  Serial2.write((uint8_t)0);      // start code
  Serial2.write(levels, show::CHANNELS);
}

void dmxTask(void*) {
  static uint8_t frame[show::CHANNELS];
  esp_task_wdt_add(nullptr);
  for (;;) {
    xSemaphoreTake(stage_lock, portMAX_DELAY);
    current_mode = stage.update(millis());
    memcpy(frame, stage.out, sizeof frame);
    xSemaphoreGive(stage_lock);
    sendFrame(frame);  // about 23 ms: some 40 frames a second
    esp_task_wdt_reset();
    vTaskDelay(1);
  }
}

// --- Ethernet ------------------------------------------------------------------
void onNetworkEvent(WiFiEvent_t event) {
  switch (event) {
    case ARDUINO_EVENT_ETH_START:
      ETH.setHostname(HOSTNAME);
      break;
    case ARDUINO_EVENT_ETH_CONNECTED:
      eth_link = true;
      link_up_at = millis();
      if (!settings.dhcp) ETH.config(settings.ip, settings.gateway, settings.mask);
      break;
    case ARDUINO_EVENT_ETH_GOT_IP:
      eth_has_ip = true;
      Serial.printf("Network: %s (%s)\n", ETH.localIP().toString().c_str(),
                    using_fallback || !settings.dhcp ? "fixed address" : "DHCP");
      break;
    case ARDUINO_EVENT_ETH_DISCONNECTED:
    case ARDUINO_EVENT_ETH_STOP:
      eth_link = false;
      eth_has_ip = false;
      Serial.println("Network: cable out");
      break;
    default:
      break;
  }
}

// No DHCP server answered (a booth switch on its own): use the fixed address.
void dhcpFallback() {
  if (settings.dhcp && eth_link && !eth_has_ip && !using_fallback && millis() - link_up_at > DHCP_WAIT_MS) {
    using_fallback = true;
    Serial.println("No DHCP server: using the fixed address");
    ETH.config(settings.ip, settings.gateway, settings.mask);
  }
}

// --- Art-Net -------------------------------------------------------------------
void pollReply(IPAddress to) {
  static uint8_t buf[artnet::POLL_REPLY_SIZE];
  IPAddress ip = ETH.localIP();
  uint8_t mac[6];
  ETH.macAddress(mac);
  char report[64];
  snprintf(report, sizeof report, "#0001 [%04u] %s", (unsigned)(packets % 10000), show::modeName(current_mode));
  artnet::NodeInfo info = {{ip[0], ip[1], ip[2], ip[3]}, {mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]},
                           settings.universe, settings.dhcp && !using_fallback, current_mode == show::LIVE,
                           "Lumiere Node", "Lumiere Node by Vital Acoustica", report};
  size_t n = artnet::buildPollReply(buf, info);
  udp.beginPacket(to, artnet::PORT);
  udp.write(buf, n);
  udp.endPacket();
}

void readArtnet() {
  static uint8_t buf[600];
  for (int i = 0; i < 20; i++) {  // a few packets per pass, then let everything else run
    int size = udp.parsePacket();
    if (size <= 0) return;
    int n = udp.read(buf, sizeof buf);
    switch (artnet::opcode(buf, n)) {
      case artnet::OP_DMX: {
        artnet::Dmx dmx;
        if (artnet::parseDmx(buf, n, dmx) && dmx.universe == settings.universe) {
          xSemaphoreTake(stage_lock, portMAX_DELAY);
          stage.onArtnet(dmx.data, dmx.length, millis());
          xSemaphoreGive(stage_lock);
          packets++;
          packets_window++;
        }
        break;
      }
      case artnet::OP_POLL:
        pollReply(udp.remoteIP());
        break;
      default:
        break;
    }
  }
}

// --- the web page ----------------------------------------------------------------
const char PAGE[] PROGMEM = R"HTML(<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lumière Node</title>
<style>
:root{--bg:#141413;--panel:#1d1d1c;--line:#2e2d2b;--text:#ece8df;--dim:#9a958b;--brass:#c9a46a}
body{margin:0;background:var(--bg);color:var(--text);font:15px -apple-system,Helvetica,Arial,sans-serif}
main{max-width:560px;margin:0 auto;padding:20px 16px 40px}
h1{font:italic 30px Georgia,serif;margin:4px 0 2px}h2{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin:26px 0 10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.row{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--line)}.row:last-child{border:0}
.row b{font-weight:600}.dim{color:var(--dim)}
label{display:block;margin:10px 0 4px;color:var(--dim);font-size:13px}
input,select{width:100%;box-sizing:border-box;background:#111;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px;font-size:15px}
button{background:linear-gradient(#2b2a28,#222120);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px 14px;font-size:15px;margin:10px 8px 0 0}
button.gold{border-color:var(--brass);color:var(--brass)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}
#mode{color:var(--brass)}</style></head><body><main>
<h1>Lumière Node</h1><div class="dim">Art-Net in, DMX out · version %VERSION%</div>
<h2>Now</h2><div class="card">
<div class="row"><span>Lights</span><b id="mode">…</b></div>
<div class="row"><span>Art-Net</span><b id="rate">…</b></div>
<div class="row"><span>Address</span><b id="ip">…</b></div>
<div class="row"><span>Universe</span><b id="uni">…</b></div>
<div class="row"><span>Running for</span><b id="up">…</b></div></div>
<button class="gold" onclick="post('/house?on='+(houseOn?0:1))" id="hb">House look</button>
<button onclick="if(confirm('Save what is on the lights now as the house look?'))post('/save-house')">Save current look as house look</button>
<h2>Settings</h2><form class="card" method="post" action="/settings">
<label>Art-Net universe (0 = the first one; some apps call it Universe 1)</label><input name="universe" type="number" min="0" max="32767" value="%UNIVERSE%">
<div class="grid"><div><label>Hold the last look (seconds)</label><input name="hold" type="number" min="0" max="600" value="%HOLD%"></div>
<div><label>Then fade to house (seconds)</label><input name="fade" type="number" min="0" max="60" step="0.5" value="%FADE%"></div></div>
<label>Network address</label><select name="dhcp"><option value="1" %AUTO%>Automatic (DHCP), or the fixed address below if there's no DHCP</option><option value="0" %FIXED%>Always the fixed address below</option></select>
<div class="grid"><div><label>Fixed address</label><input name="ip" value="%IP%"></div><div><label>Netmask</label><input name="mask" value="%MASK%"></div></div>
<label>Update password (for network updates)</label><input name="otapw" value="%OTAPW%">
<button class="gold" type="submit">Save</button><span class="dim"> The Node restarts if the network changes.</span></form>
<script>let houseOn=false;
function post(u){fetch(u,{method:'POST'}).then(tick)}
function dur(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h?h+' h '+m+' min':m+' min '+(s%60)+' s'}
function tick(){fetch('/status').then(r=>r.json()).then(s=>{houseOn=s.house_forced;
mode.textContent=s.mode+(s.house_forced?' (HOUSE button)':'');rate.textContent=s.signal?s.fps.toFixed(0)+' packets/s':'nothing arriving';
ip.textContent=s.link?(s.ip+' · '+s.how):'cable out';uni.textContent=s.universe;up.textContent=dur(s.uptime);
hb.textContent=houseOn?'Back to live':'House look'})}
tick();setInterval(tick,1000)</script></main></body></html>)HTML";

String page() {
  String html = FPSTR(PAGE);
  html.replace("%VERSION%", NODE_VERSION);
  html.replace("%UNIVERSE%", String(settings.universe));
  html.replace("%HOLD%", String(settings.hold_ms / 1000.0f, 0));
  html.replace("%FADE%", String(settings.fade_ms / 1000.0f, 1));
  html.replace("%AUTO%", settings.dhcp ? "selected" : "");
  html.replace("%FIXED%", settings.dhcp ? "" : "selected");
  html.replace("%IP%", settings.ip.toString());
  html.replace("%MASK%", settings.mask.toString());
  html.replace("%OTAPW%", settings.ota_password);
  return html;
}

void setupWeb() {
  web.on("/", HTTP_GET, [] { web.send(200, "text/html; charset=utf-8", page()); });
  web.on("/status", HTTP_GET, [] {
    uint32_t now = millis();
    bool signal, forced;
    xSemaphoreTake(stage_lock, portMAX_DELAY);
    signal = stage.signal(now);
    forced = stage.houseForced();
    xSemaphoreGive(stage_lock);
    char json[320];
    snprintf(json, sizeof json,
             "{\"mode\":\"%s\",\"signal\":%s,\"fps\":%.1f,\"house_forced\":%s,\"link\":%s,\"ip\":\"%s\","
             "\"how\":\"%s\",\"universe\":%u,\"uptime\":%lu,\"packets\":%lu,\"version\":\"%s\"}",
             show::modeName(current_mode), signal ? "true" : "false", packets_per_s, forced ? "true" : "false",
             eth_has_ip ? "true" : "false", ETH.localIP().toString().c_str(),
             (settings.dhcp && !using_fallback) ? "DHCP" : "fixed", settings.universe,
             (unsigned long)(now / 1000), (unsigned long)packets, NODE_VERSION);
    web.send(200, "application/json", json);
  });
  web.on("/house", HTTP_POST, [] {
    xSemaphoreTake(stage_lock, portMAX_DELAY);
    stage.setHouseForced(web.arg("on") == "1", millis());
    xSemaphoreGive(stage_lock);
    web.send(200, "text/plain", "ok");
  });
  web.on("/save-house", HTTP_POST, [] {
    saveHouseLook();
    web.send(200, "text/plain", "ok");
  });
  web.on("/settings", HTTP_POST, [] {
    bool network_changed = false;
    settings.universe = constrain(web.arg("universe").toInt(), 0, 32767);
    settings.hold_ms = constrain((long)(web.arg("hold").toFloat() * 1000), 0L, 600000L);
    settings.fade_ms = constrain((long)(web.arg("fade").toFloat() * 1000), 0L, 60000L);
    bool dhcp = web.arg("dhcp") == "1";
    IPAddress ip, mask;
    if (ip.fromString(web.arg("ip")) && mask.fromString(web.arg("mask"))) {
      network_changed = ip != settings.ip || mask != settings.mask;
      settings.ip = ip;
      settings.mask = mask;
    }
    network_changed |= dhcp != settings.dhcp;
    settings.dhcp = dhcp;
    if (web.arg("otapw").length()) settings.ota_password = web.arg("otapw");
    saveSettings();
    xSemaphoreTake(stage_lock, portMAX_DELAY);
    stage.hold_ms = settings.hold_ms;
    stage.fade_ms = settings.fade_ms;
    xSemaphoreGive(stage_lock);
    web.sendHeader("Location", "/");
    web.send(303);
    if (network_changed) {
      delay(300);
      ESP.restart();
    }
  });
  web.begin();
}

// --- updates over the network --------------------------------------------------------
// Start Ethernet (again, if an earlier try failed).
// Is the 50 MHz clock arriving on IO0? Sampled far slower than it ticks, a
// running clock reads as a mix of highs and lows; a dead one reads all the same.
bool ethClockRunning() {
  pinMode(0, INPUT);
  int highs = 0;
  for (int i = 0; i < 2000; i++) highs += digitalRead(0);
  return highs > 20 && highs < 1980;
}

void startEthernet() {
  pinMode(ETH_CLOCK_ENABLE, OUTPUT);
  digitalWrite(ETH_CLOCK_ENABLE, HIGH);
  delay(50);  // let the clock settle
  if (!ethClockRunning()) {
    Serial.println("Ethernet clock: NOT running on IO0. Check that nothing is connected to IO0, "
                   "and that the board gets a steady 5 V (try a USB charger on 5V/GND).");
  } else if (!eth_started && eth_retry_at == 0) {
    Serial.println("Ethernet clock: running");
  }
  eth_started = ETH.begin(ETH_ADDR, -1, ETH_MDC, ETH_MDIO, ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN);
  if (!eth_started) {
    // The Ethernet driver can't be started twice in one run, so the retry is a
    // full restart (DMX keeps going with the house look until then).
    Serial.println("Ethernet didn't start; restarting in 10 s to try again");
    eth_retry_at = millis() + 10000;
  }
}

void setupOta() {
  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(settings.ota_password.c_str());
  // An update takes longer than the watchdog allows; keep it fed while it runs.
  ArduinoOTA.onProgress([](unsigned int, unsigned int) { esp_task_wdt_reset(); });
  ArduinoOTA.onStart([] { Serial.println("Updating..."); });
  ArduinoOTA.onEnd([] { Serial.println("Update done, restarting"); });
  ArduinoOTA.begin();
  MDNS.addService("http", "tcp", 80);
}

// ------------------------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial.printf("\nLumière Node %s\n", NODE_VERSION);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_DMX_DE, OUTPUT);
  digitalWrite(PIN_DMX_DE, HIGH);

  stage_lock = xSemaphoreCreateMutex();
  loadSettings();

  esp_task_wdt_init(WATCHDOG_S, true);
  esp_task_wdt_add(nullptr);  // this loop

  // DMX starts straight away with the house look, before the network is up.
  Serial2.begin(250000, SERIAL_8N2, -1, PIN_DMX_TX);
  xTaskCreatePinnedToCore(dmxTask, "dmx", 4096, nullptr, 3, nullptr, 1);

  WiFi.onEvent(onNetworkEvent);
  startEthernet();
  window_start = millis();
}

void loop() {
  uint32_t now = millis();
  esp_task_wdt_reset();
  if (!eth_started && (int32_t)(now - eth_retry_at) >= 0) ESP.restart();
  // Art-Net, the web page and updates start once the network is up (they need it).
  if (eth_has_ip && !services_started) {
    services_started = true;
    udp.begin(artnet::PORT);
    setupWeb();
    setupOta();
    Serial.printf("Ready: http://%s/ or http://%s.local/\n", ETH.localIP().toString().c_str(), HOSTNAME);
  }
  if (services_started) {
    readArtnet();
    web.handleClient();
    ArduinoOTA.handle();
  }
  dhcpFallback();

  switch (button.update(digitalRead(PIN_BUTTON) == LOW, now)) {
    case show::SHORT_PRESS:
      xSemaphoreTake(stage_lock, portMAX_DELAY);
      stage.setHouseForced(!stage.houseForced(), now);
      xSemaphoreGive(stage_lock);
      break;
    case show::LONG_PRESS:
      saveHouseLook();
      for (int i = 0; i < 6; i++) {  // three quick flashes to say it's saved
        digitalWrite(PIN_LED, i % 2 == 0);
        delay(120);
      }
      break;
    default:
      break;
  }
  digitalWrite(PIN_LED, show::ledOn(current_mode, eth_has_ip, now));

  if (now - window_start >= 1000) {
    packets_per_s = packets_window * 1000.0f / (now - window_start);
    packets_window = 0;
    window_start = now;
  }
  delay(1);
}
