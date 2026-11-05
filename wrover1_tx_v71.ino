// wrover1_tx_v7.ino — LoRa TX with runtime-configurable POWER, DELAY + chunk retry
// Accepts CMD:POWER=X, CMD:DELAY=X, CMD:START, CMD:RETRY=N before/after frames.
// CMD:RETRY=N  re-sends chunk N (1-based) using the stored last frame.
// Pipeline: CMD:POWER → CMD:DELAY → CMD:START → <byte_count>\n<hex_payload>
//           → [CMD:RETRY=1] → [CMD:RETRY=K]

#include <HardwareSerial.h>
HardwareSerial rylr(1);

#define RYLR_RX_PIN   18
#define RYLR_TX_PIN   19
#define MY_ADDR        1
#define DEST_ADDR      2
#define CHUNK_SIZE   110

// ─── Runtime params ──────────────────────────────────────────────────────────
static int  g_power_dBm    = 22;
static int  g_delay_ms     = 915;
static bool configured     = false;

// ─── Stored frame for retry ──────────────────────────────────────────────────
// WROVER has 320 KB SRAM + 4 MB PSRAM — 16 KB global buffer is safe.
static uint8_t g_frame_buf[16384];
static int     g_frame_len    = 0;
static uint8_t g_lic_byte     = 0xAB;
static int     g_total_chunks = 0;

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

// ─── Build and send one chunk (0-indexed c) ──────────────────────────────────
// Used by both transmit_frame() and CMD:RETRY.
void send_chunk(int c) {
  int offset = c * CHUNK_SIZE;
  int clen   = min(CHUNK_SIZE, g_frame_len - offset);
  String hex = "";
  char h[3];
  sprintf(h, "%02X", (uint8_t)c);              hex += h;
  sprintf(h, "%02X", (uint8_t)g_total_chunks); hex += h;
  sprintf(h, "%02X", g_lic_byte);              hex += h;
  for (int i = 0; i < clen; i++) {
    char hb[3]; sprintf(hb, "%02X", g_frame_buf[offset + i]); hex += hb;
  }
  String cmd = "AT+SEND=" + String(DEST_ADDR) + "," +
               String(hex.length()) + "," + hex;
  sendAT(cmd.c_str(), 3000);
  delay(g_delay_ms);
}

// ─── Transmit full frame ─────────────────────────────────────────────────────
void transmit_frame(const uint8_t* data, int data_len, uint8_t lic_byte) {
  // Store frame for later CMD:RETRY calls
  if (data_len > (int)sizeof(g_frame_buf)) data_len = sizeof(g_frame_buf);
  memcpy(g_frame_buf, data, data_len);
  g_frame_len    = data_len;
  g_lic_byte     = lic_byte;
  g_total_chunks = (data_len + CHUNK_SIZE - 1) / CHUNK_SIZE;

    delay(600);    // RF settle — matches prior full-run baseline
  for (int c = 0; c < g_total_chunks; c++) {
    send_chunk(c);
  }
  Serial.println("FRAME_DONE");
}

// ─── Retransmit one chunk by 1-based index ───────────────────────────────────
void retry_chunk(int chunk_1based) {
  int c = chunk_1based - 1;   // convert to 0-based
  if (g_frame_len == 0 || c < 0 || c >= g_total_chunks) {
    Serial.print("[ERR] RETRY bad idx="); Serial.println(chunk_1based);
    return;
  }
  Serial.print("[TX] RETRY chunk="); Serial.print(chunk_1based);
  Serial.print("/"); Serial.println(g_total_chunks);
  send_chunk(c);
  Serial.println("[TX] RETRY_DONE");
}

// ─── Parse CMD lines from pipeline ──────────────────────────────────────────
bool try_parse_cmd(const String& line) {
  if (!line.startsWith("CMD:")) return false;

  if (line.startsWith("CMD:POWER=")) {
    g_power_dBm = line.substring(10).toInt();
    Serial.print("[CFG] POWER="); Serial.println(g_power_dBm);
    configured = false;
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
  if (line.startsWith("CMD:RETRY=")) {
    int n = line.substring(10).toInt();
    retry_chunk(n);
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

  String peek = Serial.readStringUntil('\n');
  peek.trim();
  if (peek.length() == 0) return;

  if (try_parse_cmd(peek)) return;

  // Frame data: first line = byte count (decimal)
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
