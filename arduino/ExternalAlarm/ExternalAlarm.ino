#include <SoftwareSerial.h>
#include <string.h>

constexpr byte bluetoothRx = 2;
constexpr byte bluetoothTx = 3;
constexpr byte buzzer = 9;
constexpr unsigned long ringDuration = 3000;

SoftwareSerial bluetooth(bluetoothRx, bluetoothTx);
char line[43];  // "ALARM " plus a 36-byte UUID and NUL.
char lastId[37] = "";
byte length = 0;
bool overflow = false;
bool ringing = false;
unsigned long started = 0;

bool validId(const char *id) {
  for (byte i = 0; i < 36; ++i) {
    char c = id[i];
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (c != '-') return false;
    } else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
      return false;
    }
  }
  return true;
}

void handleLine() {
  if (overflow || length != 42) return;
  line[length] = '\0';
  if (strncmp(line, "ALARM ", 6) != 0 || !validId(line + 6)) return;
  const char *id = line + 6;
  if (strcmp(id, lastId) != 0) {
    strcpy(lastId, id);
    started = millis();
    ringing = true;
    tone(buzzer, 2400);
  }
  bluetooth.print(F("OK "));
  bluetooth.println(id);
}

void setup() {
  pinMode(buzzer, OUTPUT);
  bluetooth.begin(9600);
}

void loop() {
  while (bluetooth.available()) {
    char c = bluetooth.read();
    if (c == '\n') {
      handleLine();
      length = 0;
      overflow = false;
    } else if (c != '\r') {
      if (length < sizeof(line) - 1) line[length++] = c;
      else overflow = true;
    }
  }
  if (ringing && millis() - started >= ringDuration) {
    noTone(buzzer);
    ringing = false;
  }
}
