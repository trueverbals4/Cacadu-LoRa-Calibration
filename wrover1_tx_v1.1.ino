// wrover1_tx_v6.ino — LoRa TX with runtime-configurable POWER and DELAY
// Accepts CMD:POWER=X and CMD:DELAY=X before each frame.
// Pipeline sends config commands, then CMD:START, then raw frame bytes.

#include <HardwareSerial.h>
HardwareSerial rylr(1);

#define RYLR_RX_PIN   18
#define RYLR_TX_PIN   19
#define MY_ADDR        1
#define DEST_ADDR      2
#define CHUNK_SIZE   110

// Runtime-configurable parameters
static int  g_power_dBm  = 22;   // AT+CRFOP value (0–22 dBm)
static int  g_delay_ms   = 915;  // inter-chunk delay after AT+SEND

static bool configured = false;

// ─── AT command helper ───────────────────────────────────────────────────────
void sendAT(const char* cmd, uint32_t timeout_ms = 1500) {
  rylr.println(cmd);
  uint32_t t = millis();
  String resp = "";
  while (millis() - t < timeout_ms) {
    if (rylr.available()) {
      String line = rylr.readStringUntil('\n');
      line.trim();
      if (line.length()) resp = line;
    }
  }
  Serial.print("AT>> "); Serial.print(cmd);
  Serial.print("  << "); Serial.println(resp);
}

// ─── Configure RYLR998 ───────────────────────────────────────────────────────
void configure() {
  delay(300);
  sendAT("AT");
  sendAT("AT+BAND=915000000");
  sendAT("AT+NETWORKID=18");
  sendAT("AT+PARAMETER=9,7,1,12");
  sendAT("AT+ADDRESS=1");
  sendAT("AT+MODE=0");

  // Runtime TX power
  char crfop_cmd[24];
  sprintf(crfop_cmd, "AT+CRFOP=%d", g_power_dBm);
  sendAT(crfop_cmd);

  configured = true;
  Serial.print("[TX] Configured — SF9 BW125 NETWORKID=18 POWER=");
  Serial.print(g_power_dBm);
  Serial.print(" dBm DELAY=");
  Serial.print(g_delay_ms);
  Serial.println("ms");
}

// ─── Transmit frame ──────────────────────────────────────────────────────────
void transmit_frame(const uint8_t* data, int data_len, uint8_t lic_byte) {
  delay(360);   // RF settle (matches prior full-run baseline)
  int total = (data_len + CHUNK_SIZE - 1) / CHUNK_SIZE;
  for (int c = 0; c < total; c++) {
    int offset = c * CHUNK_SIZE;
    int clen   = min(CHUNK_SIZE, data_len - offset);
    String hex = "";
    char h[3];
    sprintf(h, "%02X", (uint8_t)c);      hex += h;
    sprintf(h, "%02X", (uint8_t)total);  hex += h;
    sprintf(h, "%02X", lic_byte);        hex += h;
    for (int i = 0; i < clen; i++) {
      char hb[3]; sprintf(hb, "%02X", data[offset + i]); hex += hb;
    }
    String cmd = "AT+SEND=" + String(DEST_ADDR) + "," +
                 String(hex.length()) + "," + hex;
    sendAT(cmd.c_str(), 3000);
    delay(g_delay_ms);
  }
  Serial.println("FRAME_DONE");
}

// ─── Parse CMD lines from pipeline ──────────────────────────────────────────
// Returns true if it was a CMD line (consumed), false if it's frame data.
bool try_parse_cmd(const String& line) {
  if (!line.startsWith("CMD:")) return false;

  if (line.startsWith("CMD:POWER=")) {
    g_power_dBm = line.substring(10).toInt();
    Serial.print("[CFG] POWER="); Serial.println(g_power_dBm);
    configured = false;   // force reconfigure
    configure();
    return true;
  }
  if (line.startsWith("CMD:DELAY=")) {
    g_delay_ms = line.substring(10).toInt();
    Serial.print("[CFG] DELAY="); Serial.println(g_delay_ms);
    return true;
  }
  if (line == "CMD:START") {
    Serial.println("[CFG] START received — ready for frame");
    return true;
  }
  return false;
}

// ─── Setup / Loop ────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  rylr.begin(115200, SERIAL_8N1, RYLR_RX_PIN, RYLR_TX_PIN);
  delay(500);
  configure();
  Serial.println("[TX] READY");
}

void loop() {
  if (!Serial.available()) return;

  // Read one line from pipeline to check for CMD
  String peek = Serial.readStringUntil('\n');
  peek.trim();
  if (peek.length() == 0) return;

  if (try_parse_cmd(peek)) return;

  // Not a CMD — treat as start of frame data (hex string of full frame).
  // Pipeline sends: <hex_length_4>\n<hex_bytes>\n
  // OR sends raw binary bytes directly after CMD:START.
  // For compatibility, accept hex-encoded frame: first line = byte count (decimal),
  // second block = hex chars of that many bytes.
  int data_len = peek.toInt();
  if (data_len <= 0 || data_len > 16384) {
    Serial.println("[ERR] Bad frame length");
    return;
  }

  // Read hex payload
  String hex_payload = "";
  uint32_t deadline = millis() + 10000;
  while ((int)hex_payload.length() < data_len * 2 && millis() < deadline) {
    if (Serial.available()) {
      hex_payload += (char)Serial.read();
    }
  }

  if ((int)hex_payload.length() < data_len * 2) {
    Serial.println("[ERR] Incomplete frame");
    return;
  }

  // Decode hex to bytes
  uint8_t* buf = (uint8_t*)malloc(data_len);
  if (!buf) { Serial.println("[ERR] malloc fail"); return; }
  for (int i = 0; i < data_len; i++) {
    char hi = hex_payload[i*2];
    char lo = hex_payload[i*2+1];
    auto hx = [](char c) -> uint8_t {
      if (c >= '0' && c <= '9') return c - '0';
      if (c >= 'A' && c <= 'F') return c - 'A' + 10;
      if (c >= 'a' && c <= 'f') return c - 'a' + 10;
      return 0;
    };
    buf[i] = (hx(hi) << 4) | hx(lo);
  }

  Serial.print("[TX] Frame received len="); Serial.println(data_len);
  transmit_frame(buf, data_len, 0xAB);
  free(buf);
}
